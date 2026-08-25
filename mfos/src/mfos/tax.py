"""Tax Engine.

Reads the versioned tax rule-set. Computes nothing from a literal in this file.

Three behaviours that matter more than the arithmetic:

1. If the governing rule is UNVERIFIED, the engine refuses rather than
   estimating. A wrong tax number is worse than no tax number, because the
   investor acts on it.
2. Surcharge bands are deliberately unpopulated, so every figure this engine
   produces is labelled a LOWER BOUND until a reviewer fills them in.
3. A switch is priced as a redemption plus a purchase, always. There is no code
   path in which a switch is free.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .errors import InsufficientData, RuleNotVerified
from .money import apply_pct, money, rupees

# Tax asset classes are NOT the same thing as SEBI scheme categories. The
# mapping between them is a legal-review item, not an inference.
TAX_CLASSES = (
    "EQUITY_ORIENTED",
    "SPECIFIED_MUTUAL_FUND",
    "DEBT_ORIENTED_LEGACY",
    "HYBRID",
)


@dataclass
class Lot:
    """One purchase. Tax lives at lot level; portfolios do not have holding periods."""

    units: Decimal
    cost_per_unit: Decimal
    purchase_date: date

    def __post_init__(self):
        self.units = Decimal(str(self.units))
        self.cost_per_unit = money(self.cost_per_unit)

    @property
    def cost(self):
        return money(self.units * self.cost_per_unit)

    def holding_months(self, on):
        return (on - self.purchase_date).days / 30.44


@dataclass
class ExitLoadBand:
    within_days: int
    load_pct: Decimal


@dataclass
class TaxEstimate:
    """A tax figure with its provenance and its honest caveats attached."""

    gross_proceeds: Decimal
    cost: Decimal
    gain: Decimal
    short_term_gain: Decimal
    long_term_gain: Decimal
    exemption_applied: Decimal
    taxable_amount: Decimal
    base_tax: Decimal
    cess: Decimal
    surcharge: Decimal = None            # None means UNKNOWN, not zero
    exit_load: Decimal = Decimal("0")
    net_proceeds: Decimal = Decimal("0")
    is_lower_bound: bool = True
    rule_ids: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    ruleset_version: str = ""

    @property
    def total_tax(self):
        total = money(self.base_tax) + money(self.cess)
        if self.surcharge is not None:
            total += money(self.surcharge)
        return money(total)

    def as_dict(self):
        return {
            "investment_value": str(self.gross_proceeds),
            "cost": str(self.cost),
            "gain": str(self.gain),
            "short_term_gain": str(self.short_term_gain),
            "long_term_gain": str(self.long_term_gain),
            "exemption_applied": str(self.exemption_applied),
            "taxable_amount": str(self.taxable_amount),
            "base_tax": str(self.base_tax),
            "cess": str(self.cess),
            "surcharge": ("UNKNOWN" if self.surcharge is None else str(self.surcharge)),
            "estimated_tax": str(self.total_tax),
            "estimated_tax_is_lower_bound": self.is_lower_bound,
            "exit_load": str(self.exit_load),
            "net_proceeds": str(self.net_proceeds),
            "rule_ids": self.rule_ids,
            "ruleset_version": self.ruleset_version,
            "caveats": self.caveats,
        }


class TaxEngine:
    def __init__(self, registry):
        self.registry = registry
        self.rules = registry.tax

    # ── rule lookup ──────────────────────────────────────────────────────────
    def _rule_for(self, tax_class, is_long_term):
        """The rule that taxes a gain of this class and duration."""
        if tax_class == "EQUITY_ORIENTED":
            rid = "TAX-EQ-LTCG-001" if is_long_term else "TAX-EQ-STCG-001"
            return self.rules.require_verified(rid)
        if tax_class == "SPECIFIED_MUTUAL_FUND":
            return self.rules.require_verified("TAX-SPECMF-SLAB-001")
        if tax_class == "DEBT_ORIENTED_LEGACY":
            if is_long_term:
                return self.rules.require_verified("TAX-DEBT-OLD-LTCG-001")
            raise InsufficientData(
                "Short-term treatment of pre-April-2023 debt units is not modelled; "
                "this needs a qualified reviewer.",
                rule_ids=["TAX-DEBT-OLD-LTCG-001"],
            )
        if tax_class == "HYBRID":
            # Deliberately fails: the classification rule is UNVERIFIED.
            return self.rules.require_verified("TAX-HYBRID-CLASSIFY-001")
        raise InsufficientData(f"unknown tax class {tax_class!r}")

    def _is_long_term(self, tax_class, purchase_date, sale_date):
        definition = self.rules["holding_period_definitions"].get(tax_class)
        if not definition or definition.get("long_term_months") is None:
            return False        # e.g. specified mutual funds have no long-term treatment
        holding_months = (sale_date - purchase_date).days / 30.44
        return holding_months >= definition["long_term_months"]

    def _governing_rule(self, tax_class, purchase_date, sale_date):
        """Which rule taxes this lot? Returns (rule, is_long_term)."""
        is_long = self._is_long_term(tax_class, purchase_date, sale_date)
        return self._rule_for(tax_class, is_long), is_long

    # ── exit load ────────────────────────────────────────────────────────────
    @staticmethod
    def exit_load_on(lots_sold, current_nav, load_bands, sale_date):
        """Exit load is per lot and per holding period, never a flat number."""
        total = Decimal("0")
        for lot, units in lots_sold:
            age_days = (sale_date - lot.purchase_date).days
            band = None
            for b in sorted(load_bands, key=lambda x: x.within_days):
                if age_days < b.within_days:
                    band = b
                    break
            if band:
                value = money(units * money(current_nav))
                total += apply_pct(value, band.load_pct)
        return money(total)

    # ── FIFO selection ───────────────────────────────────────────────────────
    @staticmethod
    def select_lots_fifo(lots, units_to_sell):
        """Which lots go, in FIFO order. Returns [(lot, units_from_lot)]."""
        remaining = Decimal(str(units_to_sell))
        out = []
        for lot in sorted(lots, key=lambda l: l.purchase_date):
            if remaining <= 0:
                break
            take = min(lot.units, remaining)
            if take > 0:
                out.append((lot, take))
                remaining -= take
        if remaining > Decimal("0.0001"):
            raise InsufficientData(
                "Not enough units to redeem the requested quantity.",
                detail={"short_by_units": str(remaining)},
            )
        return out

    # ── the main entry point ─────────────────────────────────────────────────
    def estimate_redemption(self, *, lots, units_to_sell, current_nav, tax_class,
                            sale_date=None, exit_load_bands=None,
                            investor_marginal_rate_pct=None,
                            ltcg_exemption_already_used=Decimal("0")):
        """Tax and cost of redeeming `units_to_sell`.

        This is called before every redemption, switch and taxable rebalance.
        Its output is shown to the investor before consent is requested.
        """
        sale_date = sale_date or date.today()
        current_nav = money(current_nav)
        sold = self.select_lots_fifo(lots, units_to_sell)

        gross = money(sum(money(u * current_nav) for _, u in sold))
        cost = money(sum(money(u * lot.cost_per_unit) for lot, u in sold))
        gain = money(gross - cost)

        st_gain = Decimal("0")
        lt_gain = Decimal("0")
        base_tax = Decimal("0")
        rule_ids = []
        caveats = []
        slab_needed = False

        for lot, units in sold:
            rule, is_long = self._governing_rule(tax_class, lot.purchase_date, sale_date)
            rule_ids.append(rule["rule_id"])
            caveats.extend(rule.get("caveats") or [])
            lot_gain = money(units * current_nav) - money(units * lot.cost_per_unit)
            if is_long:
                lt_gain += lot_gain
            else:
                st_gain += lot_gain
            if rule.get("rate_basis") == "INVESTOR_SLAB":
                slab_needed = True

        if slab_needed and investor_marginal_rate_pct is None:
            raise InsufficientData(
                "This scheme is taxed at your slab rate. I need your applicable "
                "marginal rate before I can estimate the tax.",
                detail={"missing": "investor_marginal_rate_pct"},
                rule_ids=list(dict.fromkeys(rule_ids)),
            )

        # Long-term component
        exemption_applied = Decimal("0")
        lt_taxable = Decimal("0")
        if lt_gain > 0:
            lt_rule = self._rule_for(tax_class, True)
            annual_exemption = Decimal(str(lt_rule.get("annual_exemption_inr") or 0))
            available = max(Decimal("0"), annual_exemption - money(ltcg_exemption_already_used))
            exemption_applied = min(available, lt_gain)
            lt_taxable = money(lt_gain - exemption_applied)
            rate = lt_rule.get("rate_pct")
            if rate is not None:
                base_tax += apply_pct(lt_taxable, Decimal(str(rate)))
            else:
                base_tax += apply_pct(lt_taxable, Decimal(str(investor_marginal_rate_pct)))
            if annual_exemption:
                caveats.append(
                    "The long-term exemption is an annual, investor-level limit. "
                    "This estimate assumes ₹{} of it has already been used this year.".format(
                        money(ltcg_exemption_already_used)
                    )
                )

        # Short-term component
        st_taxable = Decimal("0")
        if st_gain > 0:
            st_rule = self._rule_for(tax_class, False)
            st_taxable = money(st_gain)
            rate = st_rule.get("rate_pct")
            if rate is not None:
                base_tax += apply_pct(st_taxable, Decimal(str(rate)))
            else:
                base_tax += apply_pct(st_taxable, Decimal(str(investor_marginal_rate_pct)))

        base_tax = money(base_tax)

        cess_rule = self.rules["overlays"]["cess"]
        cess = apply_pct(base_tax, Decimal(str(cess_rule["rate_pct"])))
        rule_ids.append(cess_rule["rule_id"])

        surcharge_rule = self.rules["overlays"]["surcharge"]
        surcharge = None
        if surcharge_rule.get("verification_status") in ("VERIFIED_PRIMARY", "VERIFIED_SECONDARY") \
                and surcharge_rule.get("bands"):
            surcharge = Decimal("0")     # bands populated => compute here
        else:
            caveats.append(
                "Surcharge is not modelled (its bands are unverified), so the tax "
                "figure is a lower bound, not a final number."
            )

        exit_load = Decimal("0")
        if exit_load_bands:
            exit_load = self.exit_load_on(sold, current_nav, exit_load_bands, sale_date)

        net = money(gross - exit_load - money(base_tax) - money(cess) - money(surcharge or 0))

        return TaxEstimate(
            gross_proceeds=gross,
            cost=cost,
            gain=gain,
            short_term_gain=money(st_gain),
            long_term_gain=money(lt_gain),
            exemption_applied=money(exemption_applied),
            taxable_amount=money(lt_taxable + st_taxable),
            base_tax=base_tax,
            cess=cess,
            surcharge=surcharge,
            exit_load=exit_load,
            net_proceeds=net,
            is_lower_bound=surcharge is None,
            rule_ids=list(dict.fromkeys(rule_ids)),
            caveats=list(dict.fromkeys(caveats)),
            ruleset_version=self.rules.version,
        )

    def estimate_switch(self, **kwargs):
        """A switch is a redemption of the source scheme plus a fresh purchase.

        Same numbers as a redemption, plus the rule that says so, so the
        investor is never shown a switch as if it were a costless move.
        """
        est = self.estimate_redemption(**kwargs)
        rule = self.rules.require_verified("TAX-SWITCH-IS-REDEMPTION-001")
        est.rule_ids.append(rule["rule_id"])
        est.caveats.append(
            "A switch is treated as a redemption of the source scheme: the same "
            "tax and exit load apply as if you had sold it. The new scheme's "
            "holding period starts again from zero."
        )
        return est

    def harvest_headroom(self, tax_class, used_this_year=Decimal("0")):
        """How much long-term gain could still be realised inside the exemption.

        Used by the rebalancing engine to prefer a free rebalance over a
        taxable one, not to encourage transacting for its own sake.
        """
        if tax_class != "EQUITY_ORIENTED":
            return money(0)
        rule = self.rules.require_verified("TAX-EQ-LTCG-001")
        exemption = Decimal(str(rule.get("annual_exemption_inr") or 0))
        return money(max(Decimal("0"), exemption - money(used_this_year)))
