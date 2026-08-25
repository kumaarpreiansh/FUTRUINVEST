"""Shared path setup and fixtures for the test suite."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
CONFIG = os.path.join(ROOT, "config")


def registry(sandbox=False):
    from mfos.ruleset import Registry
    reg = Registry(config_root=CONFIG)
    if sandbox:
        reg.enable_sandbox_advisory("test")
    return reg


def sample_profile(**overrides):
    from decimal import Decimal
    from mfos.profile import InvestorProfile
    defaults = dict(
        investor_id="TEST-1", age=34,
        monthly_income=Decimal("200000"), monthly_expenses=Decimal("100000"),
        current_savings=Decimal("400000"), existing_investments=Decimal("1500000"),
        total_loan_emi=Decimal("30000"), emergency_fund=Decimal("600000"),
        investment_experience="INTERMEDIATE", primary_goals=["RETIREMENT"],
        primary_horizon_years=20, dependants=1, income_stability="STABLE",
        marginal_tax_rate_pct=30.0, existing_sips=Decimal("40000"),
        life_insurance_cover=Decimal("20000000"), health_insurance_cover=Decimal("1000000"),
        risk_tolerance_answers={
            "drop_reaction": "HOLD", "acceptable_annual_loss": "20",
            "experience": "2_5Y", "volatility_preference": "MIXED_9PCT",
        },
        past_market_behaviour="HELD",
    )
    defaults.update(overrides)
    return InvestorProfile(**defaults)


def sample_goal(**overrides):
    from decimal import Decimal
    from mfos.goals import Goal
    defaults = dict(
        goal_id="G1", name="Retirement", goal_type="RETIREMENT",
        target_amount_today=Decimal("20000000"), years_to_goal=20,
        current_corpus=Decimal("1500000"), monthly_contribution=Decimal("50000"),
        annual_step_up_pct=10,
    )
    defaults.update(overrides)
    return Goal(**defaults)


# The synthetic universe carries eight years of daily NAV for eighteen schemes,
# so building and scoring it is the slowest thing in the suite. Cache both for
# the read-only tests; anything that mutates a scheme builds its own.
_UNIVERSE = None
_SCORES = None


def universe():
    global _UNIVERSE
    if _UNIVERSE is None:
        from mfos.synthetic import demo_universe
        _UNIVERSE = demo_universe()
    return _UNIVERSE


def scores():
    global _SCORES
    if _SCORES is None:
        from mfos.scoring import score_universe
        _SCORES = score_universe(universe(), registry())
    return _SCORES
