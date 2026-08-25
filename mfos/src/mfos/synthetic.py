"""SYNTHETIC scheme data — clearly labelled, for demos and tests only.

None of this is real. No NAV, AUM, expense ratio, manager name or holding in
this module corresponds to any actual scheme. It exists so the pipeline can be
run and tested end to end without a live data licence, and so that a reader can
see the shape of the data the real feeds must supply.

Every scheme produced here is stamped source='SYNTHETIC', which sits at the
bottom of the source hierarchy and is rejected by the production data-quality
gate. That is intentional: this data cannot leak into a real recommendation.
"""

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from .funds import Scheme, SchemeUniverse
from .tax import ExitLoadBand

SYNTHETIC_SOURCE = "AMFI"      # pretend feed rank, so the demo can run the gate

CATEGORY_SPECS = {
    "EQUITY_FLEXI_CAP":   {"asset_class": "EQUITY_FLEXI", "drift": 0.00045, "vol": 0.0105,
                           "riskometer": "VERY_HIGH", "tax_class": "EQUITY_ORIENTED"},
    "EQUITY_LARGE_CAP":   {"asset_class": "EQUITY_LARGE", "drift": 0.00040, "vol": 0.0095,
                           "riskometer": "VERY_HIGH", "tax_class": "EQUITY_ORIENTED"},
    "EQUITY_MID_CAP":     {"asset_class": "EQUITY_MID", "drift": 0.00048, "vol": 0.0130,
                           "riskometer": "VERY_HIGH", "tax_class": "EQUITY_ORIENTED"},
    "EQUITY_SMALL_CAP":   {"asset_class": "EQUITY_SMALL", "drift": 0.00050, "vol": 0.0160,
                           "riskometer": "VERY_HIGH", "tax_class": "EQUITY_ORIENTED"},
    "DEBT_SHORT_DURATION": {"asset_class": "DEBT_SHORT", "drift": 0.00026, "vol": 0.0012,
                            "riskometer": "LOW_TO_MODERATE", "tax_class": "SPECIFIED_MUTUAL_FUND"},
    "DEBT_CORPORATE_BOND": {"asset_class": "DEBT_MEDIUM", "drift": 0.00028, "vol": 0.0025,
                            "riskometer": "MODERATE", "tax_class": "SPECIFIED_MUTUAL_FUND"},
    "GOLD_ETF_FOF":       {"asset_class": "GOLD", "drift": 0.00030, "vol": 0.0090,
                           "riskometer": "HIGH", "tax_class": "SPECIFIED_MUTUAL_FUND"},
}

SECTORS = ["Financials", "IT", "Energy", "Consumer", "Healthcare", "Industrials", "Materials"]
STOCKS = [f"HOLDING_{i:02d}" for i in range(1, 41)]


def _series(start, days, drift, vol, rng, start_nav=100.0):
    nav, out = start_nav, []
    for i in range(days):
        nav *= (1 + rng.gauss(drift, vol))
        out.append((start + timedelta(days=i), round(nav, 4)))
    return out


def make_scheme(code, name, amc, category, *, seed=0, years=8, plan="DIRECT",
                skill=0.0, expense_ratio=0.75, aum_cr=5000.0, style="ACTIVE",
                manager_years=5.0, manager_changes=0, top10=45.0, turnover=60.0,
                exit_load=True, drift_mandate=False):
    """One synthetic scheme with a full NAV and benchmark history.

    `skill` shifts the fund's daily drift relative to its benchmark; positive
    means it genuinely outperforms, which lets tests assert that the scoring
    engine actually finds skill rather than noise.
    """
    rng = random.Random(seed)
    spec = CATEGORY_SPECS[category]
    days = int(years * 365)
    start = date.today() - timedelta(days=days)

    bench = _series(start, days, spec["drift"], spec["vol"], random.Random(seed + 10_000))
    fund_rng = random.Random(seed)
    # A passive scheme's only deviation from its index is fee drag plus a
    # little friction; an active one takes real idiosyncratic risk.
    idio_vol = spec["vol"] * (0.02 if style == "PASSIVE" else 0.35)
    nav, hist = 100.0, []
    for i, (d, _) in enumerate(bench):
        bench_ret = (bench[i][1] / bench[i - 1][1] - 1) if i else 0.0
        idio = fund_rng.gauss(skill, idio_vol)
        daily_fee = (expense_ratio / 100.0) / 252.0
        nav *= (1 + bench_ret + idio - daily_fee)
        hist.append((d, round(nav, 4)))

    now = datetime.utcnow()
    scheme = Scheme(
        scheme_code=code,
        scheme_name=name,
        amc=amc,
        plan=plan,
        sebi_category=category,
        asset_class=spec["asset_class"],
        tax_class=spec["tax_class"],
        management_style=style,
        benchmark_code=f"BM_{category}",
        riskometer=spec["riskometer"],
        launch_date=start,
        nav_date=hist[-1][0],
        manager_changes_5y=manager_changes,
        fund_manager=f"Manager {code}",
        manager_since=date.today() - timedelta(days=int(manager_years * 365)),
        top10_weight_pct=top10,
        portfolio_turnover_pct=turnover,
        illiquid_weight_pct=round(rng.uniform(0.0, 4.0), 2),
        nav_history=hist,
        benchmark_history=bench,
    )

    holdings = {}
    picks = rng.sample(STOCKS, 20)
    weights = sorted([rng.uniform(1, 10) for _ in picks], reverse=True)
    total = sum(weights)
    for stock, w in zip(picks, weights):
        holdings[stock] = round(w / total * 100, 2)
    drift_fix = round(100 - sum(holdings.values()), 2)
    holdings[picks[0]] = round(holdings[picks[0]] + drift_fix, 2)

    scheme.sector_allocation = {
        s: round(v, 2) for s, v in zip(SECTORS, _normalised([rng.uniform(5, 25) for _ in SECTORS]))
    }
    if category == "EQUITY_LARGE_CAP":
        scheme.market_cap_allocation = ({"LARGE": 60.0, "MID": 25.0, "SMALL": 15.0}
                                        if drift_mandate else
                                        {"LARGE": 88.0, "MID": 9.0, "SMALL": 3.0})
    elif category == "EQUITY_MID_CAP":
        scheme.market_cap_allocation = {"LARGE": 20.0, "MID": 70.0, "SMALL": 10.0}
    elif category == "EQUITY_SMALL_CAP":
        scheme.market_cap_allocation = {"LARGE": 8.0, "MID": 20.0, "SMALL": 72.0}
    elif category == "EQUITY_FLEXI_CAP":
        scheme.market_cap_allocation = {"LARGE": 65.0, "MID": 22.0, "SMALL": 13.0}

    scheme.observe("nav", hist[-1][1], SYNTHETIC_SOURCE, now - timedelta(hours=6))
    scheme.observe("nav", hist[-1][1], "RTA", now - timedelta(hours=7))
    scheme.observe("expense_ratio_pct", expense_ratio, "AMC_FACTSHEET", now - timedelta(hours=48))
    scheme.observe("aum_cr", aum_cr, "AMFI", now - timedelta(hours=30))
    scheme.observe("holdings", holdings, "AMC_FACTSHEET", now - timedelta(hours=400))
    if exit_load and category.startswith("EQUITY"):
        scheme.exit_load_bands = [ExitLoadBand(365, Decimal("1.0"))]
    return scheme


def _normalised(values):
    total = sum(values)
    return [v / total * 100 for v in values]


def demo_universe():
    """A small, deliberately varied synthetic universe.

    Includes schemes that SHOULD score badly — an expensive Regular plan, a
    closet index fund, a mandate-drifting large cap, a fragile micro-AUM fund —
    so the engines can be seen rejecting things, not just approving them.
    """
    u = SchemeUniverse()
    specs = [
        # code,  name,                              amc,        category,           seed, skill,   er,   aum,  extras
        ("FLX01", "Meridian Flexi Cap Direct",       "Meridian", "EQUITY_FLEXI_CAP",   1,  0.00012, 0.68, 18500, {}),
        ("FLX02", "Northwind Flexi Cap Direct",      "Northwind", "EQUITY_FLEXI_CAP",  2,  0.00004, 0.92,  9200, {}),
        ("FLX03", "Kestrel Flexi Cap Direct",        "Kestrel",  "EQUITY_FLEXI_CAP",   3, -0.00010, 1.35,  3100, {"manager_changes": 2, "manager_years": 1.2}),
        ("FLX04", "Meridian Flexi Cap Regular",      "Meridian", "EQUITY_FLEXI_CAP",   1,  0.00012, 1.78, 18500, {"plan": "REGULAR"}),
        ("FLX05", "Aster Flexi Cap Direct",          "Aster",    "EQUITY_FLEXI_CAP",   5,  0.00002, 0.55, 26000, {"top10": 62.0}),
        ("FLX06", "Corvus Flexi Cap Direct",         "Corvus",   "EQUITY_FLEXI_CAP",   6, -0.00004, 0.81,   180, {}),
        ("FLX07", "Halcyon Flexi Cap Direct",        "Halcyon",  "EQUITY_FLEXI_CAP",   7,  0.00007, 0.74, 12400, {}),
        ("FLX08", "Vireo Flexi Cap Direct",          "Vireo",    "EQUITY_FLEXI_CAP",   8, -0.00002, 1.05,  6600, {"turnover": 210.0}),
        ("LRG01", "Meridian Large Cap Direct",       "Meridian", "EQUITY_LARGE_CAP",   11, 0.00002, 0.62, 22000, {}),
        ("LRG02", "Aster Large Cap Direct",          "Aster",    "EQUITY_LARGE_CAP",   12, -0.00003, 0.88, 8800, {"drift_mandate": True}),
        ("LRG03", "Sentinel Nifty 100 Index Direct", "Sentinel", "EQUITY_LARGE_CAP",   13, -0.00001, 0.18, 4300, {"style": "PASSIVE", "exit_load": False, "top10": 55.0, "turnover": 15.0}),
        ("LRG04", "Northwind Large Cap Direct",      "Northwind", "EQUITY_LARGE_CAP",  14, 0.00005, 0.71, 15100, {}),
        ("MID01", "Kestrel Mid Cap Direct",          "Kestrel",  "EQUITY_MID_CAP",     21, 0.00009, 0.79,  9700, {}),
        ("MID02", "Halcyon Mid Cap Direct",          "Halcyon",  "EQUITY_MID_CAP",     22, -0.00006, 1.12, 5400, {}),
        ("SML01", "Corvus Small Cap Direct",         "Corvus",   "EQUITY_SMALL_CAP",   31, 0.00008, 0.94,  6200, {}),
        ("DBT01", "Meridian Corporate Bond Direct",  "Meridian", "DEBT_CORPORATE_BOND", 41, 0.00002, 0.32, 11000, {"exit_load": False, "top10": 38.0, "turnover": 90.0}),
        ("DBT02", "Aster Short Duration Direct",     "Aster",    "DEBT_SHORT_DURATION", 42, 0.00001, 0.28, 7400, {"exit_load": False, "top10": 35.0, "turnover": 110.0}),
        ("GLD01", "Sentinel Gold Fund of Fund",      "Sentinel", "GOLD_ETF_FOF",       51, 0.00000, 0.42, 2900, {"exit_load": False, "top10": 100.0, "turnover": 5.0, "style": "PASSIVE"}),
    ]
    for code, name, amc, category, seed, skill, er, aum, extras in specs:
        u.add(make_scheme(code, name, amc, category, seed=seed, skill=skill,
                          expense_ratio=er, aum_cr=aum, **extras))
    return u
