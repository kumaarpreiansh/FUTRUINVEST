"""Deterministic performance mathematics.

Nothing in this module is a model, an opinion or a prediction. It is arithmetic
over an observed NAV series, and it is the ONLY place allowed to produce a
performance number. The LLM layer may describe these outputs; it may never
generate them.

A NAV series is a list of (datetime.date, float) in ascending date order.
Every function returns None rather than a fabricated value when the input is
too short or degenerate, so callers are forced to handle "we don't know".
"""

import math
from datetime import date

TRADING_DAYS = 252.0
DAYS_IN_YEAR = 365.25


# ── series helpers ───────────────────────────────────────────────────────────

def _clean(series):
    out = [(d, float(v)) for d, v in series if v is not None and float(v) > 0]
    out.sort(key=lambda p: p[0])
    return out


def years_between(start, end):
    return (end - start).days / DAYS_IN_YEAR


def nav_on_or_before(series, target):
    """Last NAV at or before `target`; None if the series starts later."""
    best = None
    for d, v in series:
        if d <= target:
            best = (d, v)
        else:
            break
    return best


def daily_returns(series):
    s = _clean(series)
    return [s[i][1] / s[i - 1][1] - 1.0 for i in range(1, len(s))]


# ── point-to-point returns ───────────────────────────────────────────────────

def absolute_return(series):
    s = _clean(series)
    if len(s) < 2:
        return None
    return s[-1][1] / s[0][1] - 1.0


def cagr(series):
    """Compound annual growth rate. Meaningless under a year, so refused."""
    s = _clean(series)
    if len(s) < 2:
        return None
    span = years_between(s[0][0], s[-1][0])
    if span < 1.0:
        return None
    return (s[-1][1] / s[0][1]) ** (1.0 / span) - 1.0


def trailing_cagr(series, years):
    """CAGR over the last `years`, or None if the history is shorter."""
    s = _clean(series)
    if len(s) < 2:
        return None
    end_date, end_nav = s[-1]
    target = date.fromordinal(end_date.toordinal() - int(years * DAYS_IN_YEAR))
    start = nav_on_or_before(s, target)
    if start is None:
        return None
    span = years_between(start[0], end_date)
    if span < 0.9 * years:
        return None
    return (end_nav / start[1]) ** (1.0 / span) - 1.0


# ── cashflow returns ─────────────────────────────────────────────────────────

def xirr(cashflows, guess=0.10):
    """Money-weighted return over irregular cashflows.

    cashflows: list of (date, amount) — outflows negative, inflows positive.
    Bisection, not Newton: slower, but it cannot diverge, and an investor's
    return figure is not the place for an iteration that sometimes explodes.
    """
    flows = [(d, float(a)) for d, a in cashflows if a]
    if len(flows) < 2:
        return None
    flows.sort(key=lambda p: p[0])
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None

    t0 = flows[0][0]

    def npv(rate):
        total = 0.0
        for d, amount in flows:
            t = (d - t0).days / DAYS_IN_YEAR
            base = 1.0 + rate
            if base <= 1e-9:
                return float("inf") if amount > 0 else float("-inf")
            total += amount / (base ** t)
        return total

    low, high = -0.9999, 10.0
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            return mid
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2.0


def sip_xirr(series, monthly_amount, day_of_month=5, end=None):
    """What a monthly SIP into this scheme actually earned.

    This is the number that matters to a SIP investor and it is routinely
    different from the scheme's point-to-point CAGR.
    """
    s = _clean(series)
    if len(s) < 24:
        return None
    end = end or s[-1][0]
    flows, units = [], 0.0
    year, month = s[0][0].year, s[0][0].month
    while True:
        try:
            when = date(year, month, day_of_month)
        except ValueError:
            when = date(year, month, 28)
        if when > end:
            break
        point = nav_on_or_before(s, when)
        if point:
            units += monthly_amount / point[1]
            flows.append((when, -float(monthly_amount)))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    if len(flows) < 12:
        return None
    final = nav_on_or_before(s, end)
    flows.append((end, units * final[1]))
    return xirr(flows)


# ── risk ─────────────────────────────────────────────────────────────────────

def volatility(series, annualise=True):
    rets = daily_returns(series)
    if len(rets) < 30:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    return sd * math.sqrt(TRADING_DAYS) if annualise else sd


def downside_deviation(series, mar=0.0):
    """Deviation of returns below a minimum acceptable return, annualised."""
    rets = daily_returns(series)
    if len(rets) < 30:
        return None
    daily_mar = (1 + mar) ** (1 / TRADING_DAYS) - 1
    below = [min(0.0, r - daily_mar) for r in rets]
    var = sum(b * b for b in below) / len(below)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def max_drawdown(series):
    """Worst peak-to-trough fall, as a positive fraction."""
    s = _clean(series)
    if len(s) < 2:
        return None
    peak, worst = s[0][1], 0.0
    for _, nav in s:
        peak = max(peak, nav)
        worst = max(worst, (peak - nav) / peak)
    return worst


def drawdown_recovery_days(series):
    """How long the worst drawdown took to recover. None if never recovered."""
    s = _clean(series)
    if len(s) < 2:
        return None
    peak, peak_date, worst, trough_date = s[0][1], s[0][0], 0.0, None
    for d, nav in s:
        if nav > peak:
            peak, peak_date = nav, d
        dd = (peak - nav) / peak
        if dd > worst:
            worst, trough_date, worst_peak, worst_peak_date = dd, d, peak, peak_date
    if trough_date is None:
        return None
    for d, nav in s:
        if d > trough_date and nav >= worst_peak:
            return (d - worst_peak_date).days
    return None


def sharpe(series, risk_free=0.065):
    r = cagr(series)
    v = volatility(series)
    if r is None or not v:
        return None
    return (r - risk_free) / v


def sortino(series, risk_free=0.065):
    r = cagr(series)
    dd = downside_deviation(series, mar=risk_free)
    if r is None or not dd:
        return None
    return (r - risk_free) / dd


# ── relative to a benchmark ──────────────────────────────────────────────────

def _aligned(series, benchmark):
    bench = dict(_clean(benchmark))
    pairs = [(d, v, bench[d]) for d, v in _clean(series) if d in bench]
    return pairs


def beta(series, benchmark):
    pairs = _aligned(series, benchmark)
    if len(pairs) < 60:
        return None
    fr = [pairs[i][1] / pairs[i - 1][1] - 1 for i in range(1, len(pairs))]
    br = [pairs[i][2] / pairs[i - 1][2] - 1 for i in range(1, len(pairs))]
    bm = sum(br) / len(br)
    fm = sum(fr) / len(fr)
    cov = sum((f - fm) * (b - bm) for f, b in zip(fr, br)) / (len(fr) - 1)
    var = sum((b - bm) ** 2 for b in br) / (len(br) - 1)
    if not var:
        return None
    return cov / var


def alpha(series, benchmark, risk_free=0.065):
    """Jensen's alpha, annualised."""
    b = beta(series, benchmark)
    rf = cagr(series)
    rb = cagr(benchmark)
    if b is None or rf is None or rb is None:
        return None
    return rf - (risk_free + b * (rb - risk_free))


def tracking_error(series, benchmark):
    pairs = _aligned(series, benchmark)
    if len(pairs) < 60:
        return None
    diffs = [
        (pairs[i][1] / pairs[i - 1][1]) - (pairs[i][2] / pairs[i - 1][2])
        for i in range(1, len(pairs))
    ]
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def capture_ratios(series, benchmark):
    """(upside, downside) capture against the benchmark's up and down months.

    Downside capture below 100% is the property that actually keeps investors
    invested, which is why it is weighted in the fund score.
    """
    pairs = _aligned(series, benchmark)
    if len(pairs) < 60:
        return (None, None)
    monthly = {}
    for d, f, b in pairs:
        monthly.setdefault((d.year, d.month), []).append((d, f, b))
    keys = sorted(monthly)
    up_f = up_b = dn_f = dn_b = 0.0
    for i in range(1, len(keys)):
        prev, cur = monthly[keys[i - 1]][-1], monthly[keys[i]][-1]
        fr = cur[1] / prev[1] - 1
        br = cur[2] / prev[2] - 1
        if br > 0:
            up_f += fr
            up_b += br
        elif br < 0:
            dn_f += fr
            dn_b += br
    up = (up_f / up_b * 100) if up_b else None
    dn = (dn_f / dn_b * 100) if dn_b else None
    return (up, dn)


# ── rolling returns: the honest way to judge consistency ─────────────────────

def rolling_returns(series, window_years, step_days=30):
    """Annualised return for every window of `window_years` in the history."""
    s = _clean(series)
    if len(s) < 2:
        return []
    window_days = int(window_years * DAYS_IN_YEAR)
    if (s[-1][0] - s[0][0]).days < window_days:
        return []
    out = []
    start_idx, n = 0, len(s)
    cursor = s[0][0]
    while True:
        end_target = date.fromordinal(cursor.toordinal() + window_days)
        if end_target > s[-1][0]:
            break
        start = nav_on_or_before(s, cursor)
        end = nav_on_or_before(s, end_target)
        if start and end and end[0] > start[0]:
            span = years_between(start[0], end[0])
            if span > 0:
                out.append((start[0], (end[1] / start[1]) ** (1 / span) - 1))
        cursor = date.fromordinal(cursor.toordinal() + step_days)
    del start_idx, n
    return out


def rolling_outperformance_rate(series, benchmark, window_years):
    """Share of rolling windows in which the fund beat its benchmark.

    One good year proves nothing. This is the metric the signal engine trusts.
    """
    fund = dict(rolling_returns(series, window_years))
    bench = dict(rolling_returns(benchmark, window_years))
    common = sorted(set(fund) & set(bench))
    if len(common) < 12:
        return None
    wins = sum(1 for d in common if fund[d] > bench[d])
    return wins / len(common)


def rolling_stats(series, window_years):
    rolls = [r for _, r in rolling_returns(series, window_years)]
    if len(rolls) < 12:
        return None
    rolls_sorted = sorted(rolls)
    mean = sum(rolls) / len(rolls)
    var = sum((r - mean) ** 2 for r in rolls) / (len(rolls) - 1)
    return {
        "windows": len(rolls),
        "mean": mean,
        "median": rolls_sorted[len(rolls_sorted) // 2],
        "min": rolls_sorted[0],
        "max": rolls_sorted[-1],
        "stdev": math.sqrt(var),
        "share_negative": sum(1 for r in rolls if r < 0) / len(rolls),
    }


def percentile_rank(value, population, higher_is_better=True):
    """Where `value` sits in `population`, 0-100. None-safe."""
    pop = [p for p in population if p is not None]
    if value is None or len(pop) < 2:
        return None
    below = sum(1 for p in pop if p < value)
    rank = below / len(pop) * 100.0
    return rank if higher_is_better else 100.0 - rank


def quartile(value, population, higher_is_better=True):
    """1 = best quartile, 4 = worst. The way Indian investors read fund tables."""
    rank = percentile_rank(value, population, higher_is_better)
    if rank is None:
        return None
    if rank >= 75:
        return 1
    if rank >= 50:
        return 2
    if rank >= 25:
        return 3
    return 4
