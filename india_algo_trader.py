#!/usr/bin/env python3
"""india_algo_trader.py — NSE algorithmic trading terminal on a zero-cost stack.

    pip install yfinance pandas nltk beautifulsoup4 requests

Two phases run back to back, automatically:

  1. BACKTEST  — 30 days of hourly bars, EMA(9)/EMA(21) crossover, simulated
                 fills, printed performance summary.
  2. LIVE      — 1-minute bars polled on a loop, VADER sentiment over
                 Moneycontrol / Economic Times RSS, hybrid signal.

Money model: one virtual wallet seeded with a fixed amount of rupees
(default ₹100). Nothing here touches a real brokerage account.

SAFETY: in live mode the app never places an order on its own. Every signal
stops the loop, prints the full trade ticket, and waits for a typed Y/N.
A non-interactive stdin counts as "no".

Educational decision-support tooling. Not financial advice. Simulated
results are not real results.
"""

import argparse
import re
import signal
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

# lxml gives a proper XML parse of RSS; without it html.parser handles these
# feeds fine (RSS tags are already lowercase) but warns on every document.
try:
    import lxml  # noqa: F401

    _RSS_PARSER = "xml"
except ImportError:
    import warnings

    from bs4 import XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    _RSS_PARSER = "html.parser"

# ── Constants ────────────────────────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))

NSE_SUFFIX = ".NS"
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)

DEFAULT_TICKER = "SUZLON"
DEFAULT_CAPITAL = 100.0
DEFAULT_LOOP_SECONDS = 60
BACKTEST_DAYS = 30

EMA_FAST = 9
EMA_SLOW = 21

# Live-mode signal thresholds (section D of the spec).
BUY_SENTIMENT = 0.15
SELL_SENTIMENT = -0.05

RSS_FEEDS = {
    "Moneycontrol/markets": "https://www.moneycontrol.com/rss/marketreports.xml",
    "Moneycontrol/business": "https://www.moneycontrol.com/rss/business.xml",
    "Moneycontrol/latest": "https://www.moneycontrol.com/rss/latestnews.xml",
    "ET/markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "ET/stocks": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
}

RSS_TIMEOUT = 10
RSS_CACHE_SECONDS = 120
USER_AGENT = "india-algo-trader/1.0 (educational paper-trading research)"

# Ticker symbols rarely appear verbatim in headlines; map them to the words
# journalists actually use. Extend as you add instruments.
TICKER_ALIASES = {
    "SUZLON": ["suzlon"],
    "RELIANCE": ["reliance", "ril", "mukesh ambani"],
    "INFY": ["infosys"],
    "TCS": ["tcs", "tata consultancy"],
    "HDFCBANK": ["hdfc bank", "hdfcbank"],
    "ICICIBANK": ["icici bank", "icicibank"],
    "SBIN": ["sbi", "state bank of india"],
    "TATAMOTORS": ["tata motors", "tatamotors"],
    "TATASTEEL": ["tata steel", "tatasteel"],
    "IDEA": ["vodafone idea", "vi ", "idea cellular"],
    "YESBANK": ["yes bank", "yesbank"],
    "ZOMATO": ["zomato", "eternal"],
    "ITC": ["itc "],
    "WIPRO": ["wipro"],
    "ADANIENT": ["adani enterprises", "adani ent"],
}

# VADER is trained on general English; these are the words that actually move
# an Indian equities desk. Scores are on VADER's -4..+4 lexicon scale.
FINANCE_LEXICON = {
    "bullish": 2.4, "bearish": -2.4,
    "upgrade": 2.0, "downgrade": -2.2,
    "outperform": 2.0, "underperform": -2.0,
    "beat": 1.8, "miss": -1.8, "missed": -1.8,
    "surge": 2.6, "surged": 2.6, "soar": 2.8, "soared": 2.8,
    "rally": 2.2, "rallied": 2.2, "jump": 1.8, "jumped": 1.8,
    "plunge": -2.8, "plunged": -2.8, "crash": -3.2, "crashed": -3.2,
    "slump": -2.4, "slumped": -2.4, "tumble": -2.4, "tumbled": -2.4,
    "slide": -1.6, "slid": -1.6, "dip": -1.2, "dipped": -1.2,
    "profit": 1.8, "loss": -1.8, "losses": -1.8,
    "gain": 1.6, "gains": 1.6, "decline": -1.6, "declined": -1.6,
    "record": 1.4, "high": 1.0, "low": -1.0,
    "buy": 1.6, "sell": -1.6, "hold": 0.0,
    "target": 0.8, "guidance": 0.4,
    "fraud": -3.4, "probe": -2.2, "raid": -2.4, "default": -2.8,
    "penalty": -2.2, "downtrend": -2.0, "uptrend": 2.0,
    "dividend": 1.4, "bonus": 1.4, "buyback": 1.6,
    "stake": 0.4, "order win": 2.2, "orderbook": 1.2,
    "multibagger": 2.6, "breakout": 2.0, "breakdown": -2.0,
}


def rupees(value: float) -> str:
    """Format a number as rupees for console output."""
    return f"₹{value:,.2f}"


# ── Transaction costs ────────────────────────────────────────────────────────

@dataclass
class Charges:
    """Statutory + broker charges on one executed leg, itemised."""

    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0
    dp: float = 0.0

    @property
    def total(self) -> float:
        return (self.brokerage + self.stt + self.exchange + self.sebi
                + self.stamp_duty + self.gst + self.dp)

    def breakdown(self) -> str:
        parts = [
            ("brokerage", self.brokerage), ("STT", self.stt),
            ("exchange", self.exchange), ("SEBI", self.sebi),
            ("stamp", self.stamp_duty), ("GST", self.gst), ("DP", self.dp),
        ]
        return ", ".join(f"{name} {rupees(value)}" for name, value in parts if value)


@dataclass
class CostModel:
    """Indian equity transaction costs, defaulted to Zerodha's published rates.

    Rates change — SEBI, the exchanges, and brokers all revise them. Treat
    these as a starting point and check the current rate card before you read
    anything into a backtest. Every field is overridable.

    'delivery' (CNC) settles into the demat account; 'intraday' (MIS) squares
    off the same session. They are taxed very differently: delivery pays STT
    on both legs and a per-scrip DP fee on every sell, intraday pays STT on
    the sell leg only but is charged brokerage.
    """

    profile: str = "delivery"
    slippage_bps: float = 5.0          # half-spread + impact, each way

    brokerage_pct: Optional[float] = None      # None -> profile default
    brokerage_cap: float = 20.0                # rupees per executed order

    stt_delivery_pct: float = 0.100            # both legs
    stt_intraday_sell_pct: float = 0.025       # sell leg only
    exchange_pct: float = 0.00297              # NSE equity turnover charge
    sebi_pct: float = 0.0001                   # ₹10 per crore
    stamp_delivery_buy_pct: float = 0.015      # buy leg only
    stamp_intraday_buy_pct: float = 0.003      # buy leg only
    gst_pct: float = 18.0                      # on brokerage + exchange + SEBI
    dp_charge: float = 15.34                   # per scrip, per day, delivery sells

    enabled: bool = True

    def __post_init__(self):
        if self.profile not in ("delivery", "intraday"):
            raise ValueError("profile must be 'delivery' or 'intraday'")

    @property
    def is_intraday(self) -> bool:
        return self.profile == "intraday"

    def fill_price(self, side: str, price: float) -> float:
        """Slippage always works against you: buys fill up, sells fill down."""
        if not self.enabled or not self.slippage_bps:
            return price
        drift = price * (self.slippage_bps / 10_000.0)
        return price + drift if side == "BUY" else price - drift

    def _brokerage(self, turnover: float) -> float:
        pct = self.brokerage_pct
        if pct is None:
            pct = 0.03 if self.is_intraday else 0.0  # Zerodha: delivery is free
        return min(turnover * pct / 100.0, self.brokerage_cap) if pct else 0.0

    def charges(self, side: str, quantity: int, price: float) -> Charges:
        """Charges on one leg. `price` should already include slippage."""
        if not self.enabled:
            return Charges()

        turnover = quantity * price
        c = Charges()
        c.brokerage = self._brokerage(turnover)

        if self.is_intraday:
            if side == "SELL":
                c.stt = turnover * self.stt_intraday_sell_pct / 100.0
            else:
                c.stamp_duty = turnover * self.stamp_intraday_buy_pct / 100.0
        else:
            c.stt = turnover * self.stt_delivery_pct / 100.0
            if side == "BUY":
                c.stamp_duty = turnover * self.stamp_delivery_buy_pct / 100.0
            else:
                c.dp = self.dp_charge

        c.exchange = turnover * self.exchange_pct / 100.0
        c.sebi = turnover * self.sebi_pct / 100.0
        c.gst = (c.brokerage + c.exchange + c.sebi) * self.gst_pct / 100.0
        return c

    def round_trip_estimate(self, quantity: int, price: float) -> float:
        """What a buy and an immediate sell at the same price would cost."""
        buy = self.fill_price("BUY", price)
        sell = self.fill_price("SELL", price)
        return (self.charges("BUY", quantity, buy).total
                + self.charges("SELL", quantity, sell).total
                + (buy - sell) * quantity)

    def describe(self) -> str:
        if not self.enabled:
            return "costs OFF (frictionless — optimistic)"
        return (f"{self.profile} · slippage {self.slippage_bps:g} bps/leg · "
                f"Zerodha-style charges")

    @classmethod
    def free(cls) -> "CostModel":
        return cls(slippage_bps=0.0, enabled=False)


def now_ist() -> datetime:
    return datetime.now(IST)


def market_is_open(when: Optional[datetime] = None) -> bool:
    """NSE cash segment: Mon-Fri, 09:15-15:30 IST. Ignores trading holidays."""
    when = when or now_ist()
    if when.weekday() >= 5:
        return False
    open_at = when.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    close_at = when.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return open_at <= when <= close_at


# ── A. Data ingestion layer ──────────────────────────────────────────────────

class MarketDataFeed:
    """yfinance-backed price feed. Every symbol is routed to the NSE via '.NS'."""

    def __init__(self, ticker: str):
        self.base = ticker.upper().replace(NSE_SUFFIX, "")
        self.symbol = self.base + NSE_SUFFIX
        self._handle = yf.Ticker(self.symbol)

    def _history(self, period: str, interval: str) -> pd.DataFrame:
        frame = self._handle.history(period=period, interval=interval, auto_adjust=False)
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        return frame.dropna(subset=["Close"])

    def historical_hourly(self, days: int = BACKTEST_DAYS) -> pd.DataFrame:
        """Hourly bars for the backtest window. yfinance caps 1h data at 730d."""
        frame = self._history(period=f"{days}d", interval="1h")
        if frame.empty:
            raise DataUnavailable(
                f"yfinance returned no hourly bars for {self.symbol}. "
                "Check the symbol spelling, or that it is listed on the NSE."
            )
        return frame

    def live_minute_bars(self) -> pd.DataFrame:
        """Recent 1-minute bars. yfinance only serves 1m data for the last ~7 days."""
        for period in ("1d", "5d"):
            frame = self._history(period=period, interval="1m")
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def latest_price(self) -> tuple[float, datetime, bool]:
        """Return (price, bar timestamp, stale). Stale means the print predates today."""
        bars = self.live_minute_bars()
        if not bars.empty:
            stamp = bars.index[-1].to_pydatetime()
            price = float(bars["Close"].iloc[-1])
            stale = stamp.astimezone(IST).date() != now_ist().date()
            return price, stamp, stale

        # Weekend / long holiday: fall back to the last daily close.
        daily = self._history(period="5d", interval="1d")
        if daily.empty:
            raise DataUnavailable(f"No price data available for {self.symbol}.")
        return float(daily["Close"].iloc[-1]), daily.index[-1].to_pydatetime(), True


class DataUnavailable(RuntimeError):
    """Raised when the free data stack cannot serve the requested bars."""


# ── B. AI & financial sentiment engine ───────────────────────────────────────

@dataclass
class NewsItem:
    title: str
    source: str
    score: float = 0.0


@dataclass
class SentimentReading:
    """One sentiment observation: the compound score plus what produced it."""

    score: float           # normalised compound polarity, -1.0 .. +1.0
    matched: int           # headlines that mentioned this ticker
    scanned: int           # headlines pulled across all feeds
    items: list[NewsItem] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=now_ist)

    @property
    def has_evidence(self) -> bool:
        return self.matched > 0

    def label(self) -> str:
        if not self.has_evidence:
            return "NO NEWS"
        if self.score >= BUY_SENTIMENT:
            return "POSITIVE"
        if self.score <= SELL_SENTIMENT:
            return "NEGATIVE"
        return "NEUTRAL"


class SentimentEngine:
    """VADER compound polarity over Indian financial RSS, filtered by ticker."""

    def __init__(self, ticker: str, cache_seconds: int = RSS_CACHE_SECONDS):
        self.ticker = ticker.upper().replace(NSE_SUFFIX, "")
        self.keywords = self._build_keywords(self.ticker)
        self.cache_seconds = cache_seconds
        self._cache: list[NewsItem] = []
        self._cached_at = 0.0
        self._analyzer = self._load_vader()

    @staticmethod
    def _build_keywords(ticker: str) -> list[str]:
        words = {ticker.lower()}
        words.update(TICKER_ALIASES.get(ticker, []))
        return [w.strip() for w in words if w.strip()]

    @staticmethod
    def _load_vader():
        """Load VADER, downloading the lexicon on first run."""
        import nltk
        from nltk.sentiment.vader import SentimentIntensityAnalyzer

        try:
            analyzer = SentimentIntensityAnalyzer()
        except LookupError:
            print("  … downloading NLTK vader_lexicon (one-time)")
            nltk.download("vader_lexicon", quiet=True)
            analyzer = SentimentIntensityAnalyzer()

        analyzer.lexicon.update(FINANCE_LEXICON)
        return analyzer

    # -- RSS ingestion --------------------------------------------------------

    @staticmethod
    def _parse_feed(xml_text: str, source: str) -> list[NewsItem]:
        """Pull <item><title> out of an RSS document."""
        soup = BeautifulSoup(xml_text, _RSS_PARSER)

        items = []
        for node in soup.find_all("item"):
            title_node = node.find("title")
            if not title_node:
                continue
            title = re.sub(r"\s+", " ", title_node.get_text()).strip()
            if title:
                items.append(NewsItem(title=title, source=source))
        return items

    def _fetch_headlines(self) -> list[NewsItem]:
        if self._cache and (time.time() - self._cached_at) < self.cache_seconds:
            return self._cache

        headlines: list[NewsItem] = []
        seen: set[str] = set()
        for source, url in RSS_FEEDS.items():
            try:
                response = requests.get(
                    url, timeout=RSS_TIMEOUT, headers={"User-Agent": USER_AGENT}
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"  ⚠ RSS {source} unavailable: {exc}")
                continue

            for item in self._parse_feed(response.text, source):
                key = item.title.lower()
                if key not in seen:
                    seen.add(key)
                    headlines.append(item)

        if headlines:
            self._cache = headlines
            self._cached_at = time.time()
        return headlines

    # -- Scoring --------------------------------------------------------------

    def _is_relevant(self, title: str) -> bool:
        text = title.lower()
        return any(word in text for word in self.keywords)

    def read(self) -> SentimentReading:
        """Fetch, filter to this ticker, and average the compound polarity."""
        headlines = self._fetch_headlines()
        relevant = [h for h in headlines if self._is_relevant(h.title)]

        for item in relevant:
            item.score = self._analyzer.polarity_scores(item.title)["compound"]

        score = sum(i.score for i in relevant) / len(relevant) if relevant else 0.0
        return SentimentReading(
            score=round(score, 4),
            matched=len(relevant),
            scanned=len(headlines),
            items=sorted(relevant, key=lambda i: abs(i.score), reverse=True)[:5],
        )


# ── Indicators ───────────────────────────────────────────────────────────────

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def crossover_signals(closes: pd.Series, fast: int = EMA_FAST, slow: int = EMA_SLOW) -> pd.DataFrame:
    """EMA crossover: +1 on a golden cross, -1 on a death cross, 0 otherwise."""
    fast_line = ema(closes, fast)
    slow_line = ema(closes, slow)
    spread = fast_line - slow_line
    previous = spread.shift(1)

    signal = pd.Series(0, index=closes.index, dtype=int)
    signal[(spread > 0) & (previous <= 0)] = 1
    signal[(spread < 0) & (previous >= 0)] = -1
    signal.iloc[: max(fast, slow)] = 0  # warm-up period is not tradeable

    return pd.DataFrame(
        {"close": closes, "ema_fast": fast_line, "ema_slow": slow_line, "signal": signal}
    )


# ── D. Paper wallet ──────────────────────────────────────────────────────────

@dataclass
class Fill:
    timestamp: datetime
    side: str
    quantity: int
    price: float               # price actually filled at, slippage included
    realized: float = 0.0      # net of charges on both legs
    charges: float = 0.0
    requested_price: float = 0.0


class PaperWallet:
    """Virtual cash + inventory ledger. Long-only, whole shares, no leverage.

    `avg_price` is the effective cost basis: the fill price plus the buy-side
    charges spread over the shares. Realised PnL is therefore already net of
    both legs' costs, and `equity()` needs no further adjustment because every
    charge has already left `cash`.
    """

    def __init__(self, capital: float, costs: Optional[CostModel] = None):
        self.starting_capital = capital
        self.cash = capital
        self.quantity = 0
        self.avg_price = 0.0
        self.realized_pnl = 0.0
        self.total_charges = 0.0
        self.total_slippage = 0.0
        self.costs = costs or CostModel.free()
        self.fills: list[Fill] = []

    # -- sizing -----------------------------------------------------------

    def outlay_for(self, quantity: int, price: float) -> float:
        """Total cash a buy of this size would consume, charges included."""
        fill = self.costs.fill_price("BUY", price)
        return quantity * fill + self.costs.charges("BUY", quantity, fill).total

    def affordable_quantity(self, price: float) -> int:
        """Largest whole-share position the cash can carry *after* charges."""
        if price <= 0:
            return 0
        fill = self.costs.fill_price("BUY", price)
        if fill <= 0:
            return 0

        quantity = int(self.cash // fill)
        # Charges are mostly proportional, so at most a share or two comes off.
        while quantity > 0 and self.outlay_for(quantity, price) > self.cash + 1e-9:
            quantity -= 1
        return quantity

    # -- mutations --------------------------------------------------------

    def buy(self, quantity: int, price: float, when: Optional[datetime] = None) -> Fill:
        if quantity <= 0:
            raise ValueError("buy quantity must be positive")

        fill_price = self.costs.fill_price("BUY", price)
        charges = self.costs.charges("BUY", quantity, fill_price).total
        outlay = quantity * fill_price + charges
        if outlay > self.cash + 1e-9:
            raise ValueError(f"insufficient cash: need {rupees(outlay)}, have {rupees(self.cash)}")

        basis = (self.avg_price * self.quantity) + (quantity * fill_price) + charges
        self.quantity += quantity
        self.avg_price = basis / self.quantity
        self.cash -= outlay
        self.total_charges += charges
        self.total_slippage += (fill_price - price) * quantity

        fill = Fill(when or now_ist(), "BUY", quantity, fill_price,
                    charges=charges, requested_price=price)
        self.fills.append(fill)
        return fill

    def sell(self, quantity: int, price: float, when: Optional[datetime] = None) -> Fill:
        if quantity <= 0:
            raise ValueError("sell quantity must be positive")
        if quantity > self.quantity:
            raise ValueError(f"cannot sell {quantity}, holding {self.quantity}")

        fill_price = self.costs.fill_price("SELL", price)
        charges = self.costs.charges("SELL", quantity, fill_price).total
        proceeds = quantity * fill_price - charges

        realized = proceeds - (self.avg_price * quantity)
        self.realized_pnl += realized
        self.cash += proceeds
        self.quantity -= quantity
        self.total_charges += charges
        self.total_slippage += (price - fill_price) * quantity
        if self.quantity == 0:
            self.avg_price = 0.0

        fill = Fill(when or now_ist(), "SELL", quantity, fill_price, realized,
                    charges=charges, requested_price=price)
        self.fills.append(fill)
        return fill

    # -- valuation --------------------------------------------------------

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_price) * self.quantity

    def equity(self, price: float) -> float:
        return self.cash + (self.quantity * price)

    def return_pct(self, price: float) -> float:
        return (self.equity(price) / self.starting_capital - 1) * 100

    def summary(self, price: float) -> str:
        return (
            f"cash {rupees(self.cash)} | qty {self.quantity} @ {rupees(self.avg_price)} "
            f"| unrealised {rupees(self.unrealized_pnl(price))} "
            f"| realised {rupees(self.realized_pnl)} "
            f"| equity {rupees(self.equity(price))}"
        )


# ── E. Broker interface ──────────────────────────────────────────────────────

@dataclass
class Order:
    """Broker-neutral order. Field names map onto the Indian retail SDKs.

    Angel One SmartAPI  ->  placeOrder({variety, tradingsymbol, symboltoken,
                                       transactiontype, exchange, ordertype,
                                       producttype, duration, price, quantity})
    Fyers API v3        ->  place_order({symbol, qty, type, side, productType,
                                         limitPrice, stopPrice, validity})
    """

    symbol: str            # "SUZLON-EQ" style trading symbol
    side: str              # BUY | SELL
    quantity: int
    price: float
    exchange: str = "NSE"
    order_type: str = "MARKET"
    product: str = "INTRADAY"
    validity: str = "DAY"
    reason: str = ""

    @property
    def value(self) -> float:
        return self.quantity * self.price


@dataclass
class OrderResult:
    accepted: bool
    order_id: str
    message: str = ""


class BrokerInterface(ABC):
    """Swap the paper broker for a live adapter without touching strategy code."""

    @abstractmethod
    def place_order(self, order: Order) -> OrderResult:
        ...

    @abstractmethod
    def describe(self) -> str:
        ...


class PaperBroker(BrokerInterface):
    """Logs the order to the console instead of routing it anywhere."""

    def __init__(self):
        self._sequence = 0

    def place_order(self, order: Order) -> OrderResult:
        self._sequence += 1
        order_id = f"PAPER-{self._sequence:05d}"
        print(
            f"  📝 PAPER FILL {order_id}: {order.side} {order.quantity} x "
            f"{order.symbol} @ {rupees(order.price)} = {rupees(order.value)}"
        )
        return OrderResult(accepted=True, order_id=order_id, message="simulated")

    def describe(self) -> str:
        return "PaperBroker (simulated fills, no external calls)"


class LiveBrokerAdapter(BrokerInterface):
    """Scaffold for a real zero-brokerage SDK. Deliberately does not execute.

    Subclass and implement `_submit` against the vendor SDK when you are ready
    to go live, with your own credentials and your own risk sign-off. This
    class builds the vendor payload so you can verify the mapping first.
    """

    vendor = "generic"

    def __init__(self, enable_live: bool = False):
        self.enable_live = enable_live

    def to_payload(self, order: Order) -> dict:
        raise NotImplementedError

    def _submit(self, payload: dict) -> OrderResult:
        raise NotImplementedError(
            f"{self.vendor} live routing is not implemented in this educational build."
        )

    def place_order(self, order: Order) -> OrderResult:
        payload = self.to_payload(order)
        if not self.enable_live:
            print(f"  🔒 LIVE DISABLED — {self.vendor} payload would have been: {payload}")
            return OrderResult(False, "", "live trading disabled")
        return self._submit(payload)

    def describe(self) -> str:
        state = "ENABLED" if self.enable_live else "disabled"
        return f"{self.vendor} adapter (live routing {state})"


class AngelOneBroker(LiveBrokerAdapter):
    vendor = "AngelOne SmartAPI"

    def to_payload(self, order: Order) -> dict:
        return {
            "variety": "NORMAL",
            "tradingsymbol": f"{order.symbol}-EQ",
            "symboltoken": "<lookup from instrument master>",
            "transactiontype": order.side,
            "exchange": order.exchange,
            "ordertype": order.order_type,
            "producttype": order.product,
            "duration": order.validity,
            "price": f"{order.price:.2f}",
            "quantity": str(order.quantity),
        }


class FyersBroker(LiveBrokerAdapter):
    vendor = "Fyers API v3"

    def to_payload(self, order: Order) -> dict:
        return {
            "symbol": f"{order.exchange}:{order.symbol}-EQ",
            "qty": order.quantity,
            "type": 2 if order.order_type == "MARKET" else 1,
            "side": 1 if order.side == "BUY" else -1,
            "productType": "INTRADAY" if order.product == "INTRADAY" else "CNC",
            "limitPrice": 0 if order.order_type == "MARKET" else order.price,
            "stopPrice": 0,
            "validity": order.validity,
            "offlineOrder": False,
        }


# ── C. Backtesting engine ────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    ticker: str
    bars: int
    start: datetime
    end: datetime
    starting_capital: float
    ending_value: float
    return_pct: float
    buy_hold_pct: float
    trades: int
    wins: int
    max_drawdown_pct: float
    never_affordable: bool
    cost_label: str = ""
    total_charges: float = 0.0
    total_slippage: float = 0.0
    gross_return_pct: float = 0.0

    @property
    def cost_drag_pct(self) -> float:
        """How many percentage points of return the friction consumed."""
        return self.gross_return_pct - self.return_pct

    def print_report(self) -> None:
        friction = self.total_charges + self.total_slippage
        print("\n" + "─" * 68)
        print(f"  BACKTEST RESULT — {self.ticker}{NSE_SUFFIX}")
        print("─" * 68)
        print(f"  Window            {self.start:%Y-%m-%d} → {self.end:%Y-%m-%d} "
              f"({self.bars} hourly bars)")
        print(f"  Strategy          EMA({EMA_FAST}) / EMA({EMA_SLOW}) crossover, long-only")
        print(f"  Cost model        {self.cost_label}")
        print(f"  Starting capital  {rupees(self.starting_capital)}")
        print(f"  Ending value      {rupees(self.ending_value)}")
        print(f"  Gross return      {self.gross_return_pct:+.2f}%  (before charges and slippage)")
        print(f"  Net return        {self.return_pct:+.2f}%")
        print(f"  Charges paid      {rupees(self.total_charges)}")
        print(f"  Slippage paid     {rupees(self.total_slippage)}")
        print(f"  Total friction    {rupees(friction)}  "
              f"({self.cost_drag_pct:.2f} pts of return)")
        print(f"  Buy & hold        {self.buy_hold_pct:+.2f}%  (same window, gross)")
        print(f"  Round trips       {self.trades}  ({self.wins} profitable after costs)")
        print(f"  Max drawdown      {self.max_drawdown_pct:.2f}%")

        if self.never_affordable:
            print(f"\n  ⚠ {rupees(self.starting_capital)} was never enough to buy a single "
                  f"share in this window,\n    so the strategy could not take a position. "
                  f"Pick a lower-priced ticker\n    or raise --capital to see the strategy trade.")
        elif friction > 0 and self.starting_capital:
            share = friction / self.starting_capital * 100
            if share > 10:
                print(f"\n  ⚠ Friction ate {share:.1f}% of starting capital. Fixed costs "
                      f"(DP fees,\n    brokerage floors) do not scale down with a small "
                      f"account — at this size\n    the charges, not the signal, decide "
                      f"the outcome.")
        print("─" * 68)


class BacktestEngine:
    """Replays the EMA crossover over historical hourly bars, bar by bar."""

    def __init__(self, feed: MarketDataFeed, capital: float, days: int = BACKTEST_DAYS,
                 costs: Optional[CostModel] = None):
        self.feed = feed
        self.capital = capital
        self.days = days
        self.costs = costs or CostModel.free()

    def run(self) -> BacktestResult:
        print(f"\n▶ Phase 1/2 — backtesting {self.feed.symbol} "
              f"over the last {self.days} days (hourly bars)…")
        print(f"  costs: {self.costs.describe()}")

        bars = self.feed.historical_hourly(self.days)
        frame = crossover_signals(bars["Close"])
        wallet = PaperWallet(self.capital, self.costs)
        frictionless = PaperWallet(self.capital, CostModel.free())

        equity_curve: list[float] = []
        never_affordable = True

        for stamp, row in frame.iterrows():
            price = float(row["close"])
            when = stamp.to_pydatetime()

            if wallet.affordable_quantity(price) > 0 or wallet.quantity > 0:
                never_affordable = False

            if row["signal"] == 1 and wallet.quantity == 0:
                quantity = wallet.affordable_quantity(price)
                if quantity > 0:
                    fill = wallet.buy(quantity, price, when)
                    # Same trade, no friction — isolates what the costs cost.
                    frictionless.buy(quantity, price, when)
                    print(f"    {when:%Y-%m-%d %H:%M}  BUY  {quantity:>3} @ "
                          f"{rupees(fill.price)}   charges {rupees(fill.charges)}")
            elif row["signal"] == -1 and wallet.quantity > 0:
                quantity = wallet.quantity
                fill = wallet.sell(quantity, price, when)
                frictionless.sell(quantity, price, when)
                print(f"    {when:%Y-%m-%d %H:%M}  SELL {quantity:>3} @ "
                      f"{rupees(fill.price)}   charges {rupees(fill.charges)}"
                      f"   net pnl {rupees(fill.realized)}")

            equity_curve.append(wallet.equity(price))

        # Mark any open position to the last close; no forced exit, just valuation.
        last_price = float(frame["close"].iloc[-1])
        first_price = float(frame["close"].iloc[0])

        sells = [f for f in wallet.fills if f.side == "SELL"]
        peak, drawdown = equity_curve[0], 0.0
        for value in equity_curve:
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak * 100 if peak else 0.0)

        return BacktestResult(
            ticker=self.feed.base,
            bars=len(frame),
            start=frame.index[0].to_pydatetime(),
            end=frame.index[-1].to_pydatetime(),
            starting_capital=self.capital,
            ending_value=wallet.equity(last_price),
            return_pct=wallet.return_pct(last_price),
            buy_hold_pct=(last_price / first_price - 1) * 100,
            trades=len(sells),
            wins=sum(1 for f in sells if f.realized > 0),
            max_drawdown_pct=drawdown,
            never_affordable=never_affordable,
            cost_label=self.costs.describe(),
            total_charges=wallet.total_charges,
            total_slippage=wallet.total_slippage,
            gross_return_pct=frictionless.return_pct(last_price),
        )


# ── D. Strategy + live trading loop ──────────────────────────────────────────

@dataclass
class Decision:
    action: str            # BUY | SELL | HOLD
    quantity: int = 0
    reason: str = ""


class HybridStrategy:
    """Sentiment decides direction; the wallet decides whether it is possible."""

    def __init__(self, buy_threshold: float = BUY_SENTIMENT, sell_threshold: float = SELL_SENTIMENT):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def decide(self, reading: SentimentReading, wallet: PaperWallet, price: float) -> Decision:
        if not reading.has_evidence:
            return Decision("HOLD", reason="no ticker-relevant headlines in this cycle")

        if reading.score > self.buy_threshold:
            if wallet.quantity > 0:
                return Decision(
                    "HOLD",
                    reason=(f"bullish ({reading.score:+.3f}) but already long "
                            f"{wallet.quantity} share(s) — no pyramiding"),
                )
            quantity = wallet.affordable_quantity(price)
            if quantity == 0:
                return Decision(
                    "HOLD",
                    reason=(f"bullish ({reading.score:+.3f}) but {rupees(wallet.cash)} "
                            f"cannot cover one share at {rupees(price)}"),
                )
            return Decision("BUY", quantity, f"sentiment {reading.score:+.3f} > {self.buy_threshold}")

        if reading.score < self.sell_threshold:
            if wallet.quantity == 0:
                return Decision(
                    "HOLD",
                    reason=(f"bearish ({reading.score:+.3f}) but flat — "
                            f"this strategy is long-only, nothing to sell"),
                )
            return Decision(
                "SELL", wallet.quantity, f"sentiment {reading.score:+.3f} < {self.sell_threshold}"
            )

        return Decision("HOLD", reason=f"sentiment {reading.score:+.3f} inside the neutral band")


class TradeConfirmation:
    """Human-in-the-loop gate. Nothing reaches the wallet without a typed Y."""

    @staticmethod
    def request(order: Order, wallet: PaperWallet, reading: SentimentReading) -> bool:
        print("\n" + "=" * 68)
        print("  ⚠  MANUAL CONFIRMATION REQUIRED — no order is placed automatically")
        print("=" * 68)
        costs = wallet.costs
        fill_price = costs.fill_price(order.side, order.price)
        charges = costs.charges(order.side, order.quantity, fill_price)

        print(f"  Action        {order.side} {order.quantity} x {order.symbol} ({order.exchange})")
        print(f"  Last price    {rupees(order.price)}   ({order.order_type}, {order.product})")
        print(f"  Est. fill     {rupees(fill_price)}   "
              f"(after {costs.slippage_bps:g} bps slippage)")
        print(f"  Order value   {rupees(order.quantity * fill_price)}")
        if charges.total:
            print(f"  Est. charges  {rupees(charges.total)}   ({charges.breakdown()})")
        print(f"  Rationale     {order.reason}")
        print(f"  Sentiment     {reading.score:+.3f} from {reading.matched} headline(s)")
        for item in reading.items[:3]:
            print(f"                • [{item.score:+.2f}] {item.title[:70]}")
        print(f"  Wallet before cash {rupees(wallet.cash)}, qty {wallet.quantity} "
              f"@ {rupees(wallet.avg_price)}")
        if order.side == "BUY":
            outlay = order.quantity * fill_price + charges.total
            print(f"  Wallet after  cash {rupees(wallet.cash - outlay)}, "
                  f"qty {wallet.quantity + order.quantity}   "
                  f"(total outlay {rupees(outlay)})")
        else:
            proceeds = order.quantity * fill_price - charges.total
            realized = proceeds - (wallet.avg_price * order.quantity)
            print(f"  Wallet after  cash {rupees(wallet.cash + proceeds)}, qty 0"
                  f"   (realising {rupees(realized)} net of charges)")
        print("=" * 68)

        if not sys.stdin.isatty():
            print("  ✋ stdin is not interactive — declining by default.")
            return False

        try:
            answer = input("  Execute this trade? [Y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  ✋ no confirmation received — declining.")
            return False

        approved = answer in ("y", "yes")
        print("  ✅ approved" if approved else "  ⛔ declined")
        return approved


class LiveTrader:
    """Polls price + sentiment on a loop and asks a human before every fill."""

    def __init__(
        self,
        feed: MarketDataFeed,
        sentiment: SentimentEngine,
        wallet: PaperWallet,
        broker: BrokerInterface,
        strategy: HybridStrategy,
        interval: int = DEFAULT_LOOP_SECONDS,
    ):
        self.feed = feed
        self.sentiment = sentiment
        self.wallet = wallet
        self.broker = broker
        self.strategy = strategy
        self.interval = interval
        self.cycle = 0
        self.last_price = 0.0

    def _log_cycle(self, price: float, stamp: datetime, stale: bool,
                   reading: SentimentReading, decision: Decision) -> None:
        flag = "  (last available print — market closed)" if stale else ""
        print(f"\n[{now_ist():%Y-%m-%d %H:%M:%S IST}] cycle {self.cycle}")
        print(f"  price      {rupees(price)}  @ {stamp.astimezone(IST):%H:%M}{flag}")
        print(f"  sentiment  {reading.score:+.3f} [{reading.label()}] "
              f"from {reading.matched}/{reading.scanned} headlines")
        print(f"  wallet     {self.wallet.summary(price)}")
        print(f"  signal     {decision.action} — {decision.reason}")

    def _execute(self, decision: Decision, price: float, reading: SentimentReading) -> None:
        order = Order(
            symbol=self.feed.base,
            side=decision.action,
            quantity=decision.quantity,
            price=price,
            reason=decision.reason,
        )

        if not TradeConfirmation.request(order, self.wallet, reading):
            return

        result = self.broker.place_order(order)
        if not result.accepted:
            print(f"  ⛔ broker rejected the order: {result.message}")
            return

        if order.side == "BUY":
            self.wallet.buy(order.quantity, order.price)
        else:
            fill = self.wallet.sell(order.quantity, order.price)
            print(f"  💰 realised {rupees(fill.realized)} on this round trip")
        print(f"  wallet     {self.wallet.summary(price)}")

    def run(self) -> None:
        print(f"\n▶ Phase 2/2 — live paper trading {self.feed.symbol}")
        print(f"  broker     {self.broker.describe()}")
        print(f"  capital    {rupees(self.wallet.starting_capital)}")
        print(f"  rules      BUY if sentiment > {self.strategy.buy_threshold} and flat; "
              f"SELL if sentiment < {self.strategy.sell_threshold} and long")
        print(f"  cadence    every {self.interval}s — press Ctrl+C to stop\n")

        if not market_is_open():
            print("  ℹ NSE is closed right now; prices will be the last available print.")

        try:
            while True:
                self.cycle += 1
                try:
                    price, stamp, stale = self.feed.latest_price()
                    self.last_price = price
                    reading = self.sentiment.read()
                    decision = self.strategy.decide(reading, self.wallet, price)
                    self._log_cycle(price, stamp, stale, reading, decision)

                    if decision.action in ("BUY", "SELL"):
                        self._execute(decision, price, reading)
                except DataUnavailable as exc:
                    print(f"  ⚠ {exc}")
                except Exception as exc:  # keep the loop alive through transient errors
                    print(f"  ⚠ cycle {self.cycle} failed: {type(exc).__name__}: {exc}")

                time.sleep(self.interval)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self) -> None:
        price = self.last_price
        print("\n\n" + "─" * 68)
        print("  SESSION ENDED (Ctrl+C)")
        print("─" * 68)
        print(f"  Cycles run        {self.cycle}")
        print(f"  Fills             {len(self.wallet.fills)}")
        print(f"  Cash              {rupees(self.wallet.cash)}")
        print(f"  Holding           {self.wallet.quantity} @ {rupees(self.wallet.avg_price)}")
        print(f"  Realised PnL      {rupees(self.wallet.realized_pnl)}")
        if price:
            print(f"  Unrealised PnL    {rupees(self.wallet.unrealized_pnl(price))}")
            print(f"  Final equity      {rupees(self.wallet.equity(price))} "
                  f"({self.wallet.return_pct(price):+.2f}%)")
        print("─" * 68)
        print("  Simulated results only. Not financial advice.\n")


# ── Entry point ──────────────────────────────────────────────────────────────

def build_broker(name: str) -> BrokerInterface:
    if name == "paper":
        return PaperBroker()
    if name == "angelone":
        return AngelOneBroker(enable_live=False)
    if name == "fyers":
        return FyersBroker(enable_live=False)
    raise ValueError(f"unknown broker: {name}")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NSE algo-trading terminal: backtest, then human-confirmed paper trading.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ticker", default=DEFAULT_TICKER, help="NSE symbol without the .NS suffix")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL,
                        help="virtual wallet size in rupees")
    parser.add_argument("--interval", type=int, default=DEFAULT_LOOP_SECONDS,
                        help="seconds between live cycles")
    parser.add_argument("--backtest-days", type=int, default=BACKTEST_DAYS,
                        help="length of the historical window")
    parser.add_argument("--broker", default="paper", choices=("paper", "angelone", "fyers"),
                        help="order sink; the live adapters are scaffolds and do not execute")
    parser.add_argument("--phase", default="both", choices=("both", "backtest", "live"),
                        help="which phases to run")
    parser.add_argument("--product", default="delivery", choices=("delivery", "intraday"),
                        help="CNC delivery or MIS intraday — they are taxed differently")
    parser.add_argument("--slippage-bps", type=float, default=5.0,
                        help="assumed slippage per leg, in basis points")
    parser.add_argument("--no-costs", action="store_true",
                        help="run frictionless (optimistic; useful only for comparison)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    print("=" * 68)
    print("  INDIA ALGO TRADER — NSE  ·  free data stack  ·  paper only")
    print(f"  ticker {args.ticker}{NSE_SUFFIX}   capital {rupees(args.capital)}   "
          f"{now_ist():%Y-%m-%d %H:%M IST}")
    print("=" * 68)

    try:
        feed = MarketDataFeed(args.ticker)
    except Exception as exc:
        print(f"⛔ could not initialise the data feed: {exc}")
        return 1

    costs = (CostModel.free() if args.no_costs
             else CostModel(profile=args.product, slippage_bps=args.slippage_bps))

    if args.phase in ("both", "backtest"):
        try:
            BacktestEngine(feed, args.capital, args.backtest_days, costs).run().print_report()
        except DataUnavailable as exc:
            print(f"⛔ backtest aborted: {exc}")
            return 1
        except Exception as exc:
            print(f"⛔ backtest failed: {type(exc).__name__}: {exc}")
            return 1

    if args.phase == "backtest":
        return 0

    print("\n  Handing off to live mode. The backtest is a simulation on past "
          "data;\n  it is not a prediction and not a reason to trust the next fill.")

    try:
        sentiment = SentimentEngine(args.ticker)
    except Exception as exc:
        print(f"⛔ sentiment engine unavailable: {exc}")
        return 1

    trader = LiveTrader(
        feed=feed,
        sentiment=sentiment,
        wallet=PaperWallet(args.capital, costs),
        broker=build_broker(args.broker),
        strategy=HybridStrategy(),
        interval=args.interval,
    )
    trader.run()
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
