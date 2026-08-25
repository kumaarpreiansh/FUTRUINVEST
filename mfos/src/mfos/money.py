"""Money and rate arithmetic.

Rupee amounts are Decimal, quantised to paise. Floats are fine for statistics
(volatility, correlations) but never for a figure shown to an investor or used
in a tax computation.
"""

from decimal import Decimal, ROUND_HALF_UP

PAISE = Decimal("0.01")
RUPEE = Decimal("1")


def money(value):
    """Coerce to a paise-quantised Decimal."""
    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))
    return d.quantize(PAISE, rounding=ROUND_HALF_UP)


def rupees(value):
    """Quantise to whole rupees — the right precision for a projection."""
    return money(value).quantize(RUPEE, rounding=ROUND_HALF_UP)


def pct(value):
    """Percent as Decimal, 2dp."""
    return money(value)


def apply_pct(amount, percent):
    """amount * percent%, in Decimal."""
    return money(money(amount) * money(percent) / Decimal("100"))


def fmt_inr(value):
    """Indian digit grouping: 1,23,45,678.90 — with the paise dropped for
    anything a lakh or above, where paise is noise."""
    d = money(value)
    negative = d < 0
    d = abs(d)
    whole = int(d)
    frac = (d - Decimal(whole)).quantize(PAISE)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    out = f"₹{s}"
    if whole < 100000 and frac:
        out = f"₹{s}.{str(frac).split('.')[1]}"
    return "-" + out if negative else out


def lakhs(value):
    """Human phrasing Indian investors actually use."""
    d = money(value)
    if abs(d) >= Decimal("10000000"):
        return f"₹{(d / Decimal('10000000')).quantize(Decimal('0.01'))} Cr"
    if abs(d) >= Decimal("100000"):
        return f"₹{(d / Decimal('100000')).quantize(Decimal('0.01'))} L"
    return fmt_inr(d)


def safe_div(numerator, denominator, default=None):
    """Division that returns `default` instead of pretending zero is a result."""
    if denominator in (0, 0.0) or denominator is None:
        return default
    return numerator / denominator
