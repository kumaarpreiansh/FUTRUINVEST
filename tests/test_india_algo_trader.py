"""Offline tests for india_algo_trader.

Everything here runs without network access: the price feed is stubbed and
the RSS layer is fed a fixture document.
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import india_algo_trader as iat


def hourly_frame(closes):
    start = datetime(2026, 7, 1, 9, 15, tzinfo=iat.IST)
    index = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(len(closes))])
    return pd.DataFrame({"Close": [float(c) for c in closes]}, index=index)


class FakeFeed:
    """Stands in for MarketDataFeed without touching yfinance."""

    def __init__(self, closes, base="TEST"):
        self.base = base
        self.symbol = base + iat.NSE_SUFFIX
        self._frame = hourly_frame(closes)

    def historical_hourly(self, days=30):
        return self._frame


class TestPaperWallet(unittest.TestCase):
    def test_buy_updates_cash_and_average(self):
        wallet = iat.PaperWallet(100.0)
        wallet.buy(3, 20.0)
        self.assertAlmostEqual(wallet.cash, 40.0)
        self.assertEqual(wallet.quantity, 3)
        self.assertAlmostEqual(wallet.avg_price, 20.0)

    def test_average_price_blends_across_buys(self):
        wallet = iat.PaperWallet(100.0)
        wallet.buy(2, 10.0)
        wallet.buy(2, 20.0)
        self.assertEqual(wallet.quantity, 4)
        self.assertAlmostEqual(wallet.avg_price, 15.0)

    def test_sell_realises_pnl_and_clears_average(self):
        wallet = iat.PaperWallet(100.0)
        wallet.buy(3, 20.0)
        fill = wallet.sell(3, 25.0)
        self.assertAlmostEqual(fill.realized, 15.0)
        self.assertAlmostEqual(wallet.realized_pnl, 15.0)
        self.assertAlmostEqual(wallet.cash, 115.0)
        self.assertEqual(wallet.quantity, 0)
        self.assertEqual(wallet.avg_price, 0.0)

    def test_affordable_quantity_is_whole_shares_only(self):
        wallet = iat.PaperWallet(100.0)
        self.assertEqual(wallet.affordable_quantity(33.0), 3)
        self.assertEqual(wallet.affordable_quantity(150.0), 0)
        self.assertEqual(wallet.affordable_quantity(0.0), 0)

    def test_cannot_overspend_or_oversell(self):
        wallet = iat.PaperWallet(100.0)
        with self.assertRaises(ValueError):
            wallet.buy(10, 50.0)
        wallet.buy(1, 50.0)
        with self.assertRaises(ValueError):
            wallet.sell(5, 60.0)

    def test_equity_and_return(self):
        wallet = iat.PaperWallet(100.0)
        wallet.buy(4, 25.0)
        self.assertAlmostEqual(wallet.equity(30.0), 120.0)
        self.assertAlmostEqual(wallet.unrealized_pnl(30.0), 20.0)
        self.assertAlmostEqual(wallet.return_pct(30.0), 20.0)


class TestIndicators(unittest.TestCase):
    def test_crossover_marks_both_directions(self):
        # Rise through the warm-up, then fall (death cross), then rise (golden cross).
        closes = list(range(20, 60)) + list(range(60, 20, -1)) + list(range(20, 60))
        frame = iat.crossover_signals(pd.Series([float(c) for c in closes]))
        self.assertIn(1, set(frame["signal"]))
        self.assertIn(-1, set(frame["signal"]))

    def test_warmup_period_is_not_tradeable(self):
        frame = iat.crossover_signals(pd.Series([float(i) for i in range(60)]))
        self.assertTrue((frame["signal"].iloc[: iat.EMA_SLOW] == 0).all())


class TestBacktestEngine(unittest.TestCase):
    def test_round_trip_is_recorded(self):
        closes = list(range(50, 20, -1)) + list(range(20, 60)) + list(range(60, 30, -1))
        result = iat.BacktestEngine(FakeFeed(closes), capital=1000.0).run()
        self.assertGreaterEqual(result.trades, 1)
        self.assertFalse(result.never_affordable)
        self.assertEqual(result.bars, len(closes))

    def test_capital_ceiling_blocks_expensive_tickers(self):
        closes = list(range(500, 470, -1)) + list(range(470, 520))
        result = iat.BacktestEngine(FakeFeed(closes), capital=100.0).run()
        self.assertTrue(result.never_affordable)
        self.assertEqual(result.trades, 0)
        self.assertAlmostEqual(result.ending_value, 100.0)
        self.assertAlmostEqual(result.return_pct, 0.0)

    def test_buy_and_hold_reference_is_computed(self):
        closes = [100.0] * 25 + [110.0] * 25
        result = iat.BacktestEngine(FakeFeed(closes), capital=1000.0).run()
        self.assertAlmostEqual(result.buy_hold_pct, 10.0)


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Suzlon Energy surges on record order win from NTPC</title></item>
  <item><title>Infosys shares plunge after guidance cut</title></item>
  <item><title>Nifty ends flat as banks offset IT losses</title></item>
</channel></rss>"""


class TestSentimentEngine(unittest.TestCase):
    def test_rss_titles_are_extracted(self):
        items = iat.SentimentEngine._parse_feed(SAMPLE_RSS, "fixture")
        self.assertEqual(len(items), 3)
        self.assertIn("Suzlon", items[0].title)

    def test_relevance_filter_uses_ticker_aliases(self):
        engine = iat.SentimentEngine.__new__(iat.SentimentEngine)
        engine.ticker = "INFY"
        engine.keywords = iat.SentimentEngine._build_keywords("INFY")
        self.assertTrue(engine._is_relevant("Infosys shares plunge after guidance cut"))
        self.assertFalse(engine._is_relevant("Suzlon Energy surges on record order win"))

    def test_reading_scores_only_relevant_headlines(self):
        engine = iat.SentimentEngine("SUZLON")
        engine._cache = iat.SentimentEngine._parse_feed(SAMPLE_RSS, "fixture")
        engine._cached_at = float("inf")

        reading = engine.read()
        self.assertEqual(reading.scanned, 3)
        self.assertEqual(reading.matched, 1)
        self.assertGreater(reading.score, 0.0)
        self.assertLessEqual(reading.score, 1.0)
        self.assertTrue(reading.has_evidence)

    def test_no_matching_news_is_neutral_and_evidence_free(self):
        engine = iat.SentimentEngine("TCS")
        engine._cache = iat.SentimentEngine._parse_feed(SAMPLE_RSS, "fixture")
        engine._cached_at = float("inf")

        reading = engine.read()
        self.assertEqual(reading.matched, 0)
        self.assertEqual(reading.score, 0.0)
        self.assertFalse(reading.has_evidence)
        self.assertEqual(reading.label(), "NO NEWS")


def reading(score, matched=2):
    return iat.SentimentReading(score=score, matched=matched, scanned=20)


class TestHybridStrategy(unittest.TestCase):
    def setUp(self):
        self.strategy = iat.HybridStrategy()

    def test_buys_when_sentiment_is_strongly_positive_and_flat(self):
        decision = self.strategy.decide(reading(0.40), iat.PaperWallet(100.0), 25.0)
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.quantity, 4)

    def test_sells_when_sentiment_turns_negative_and_long(self):
        wallet = iat.PaperWallet(100.0)
        wallet.buy(4, 25.0)
        decision = self.strategy.decide(reading(-0.20), wallet, 26.0)
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.quantity, 4)

    def test_neutral_band_holds(self):
        decision = self.strategy.decide(reading(0.05), iat.PaperWallet(100.0), 25.0)
        self.assertEqual(decision.action, "HOLD")

    def test_no_headlines_means_no_trade(self):
        decision = self.strategy.decide(reading(0.9, matched=0), iat.PaperWallet(100.0), 25.0)
        self.assertEqual(decision.action, "HOLD")

    def test_does_not_double_up_on_an_existing_position(self):
        wallet = iat.PaperWallet(100.0)
        wallet.buy(4, 25.0)
        decision = self.strategy.decide(reading(0.9), wallet, 25.0)
        self.assertEqual(decision.action, "HOLD")
        self.assertIn("already long", decision.reason)

    def test_bearish_while_flat_reports_long_only(self):
        decision = self.strategy.decide(reading(-0.9), iat.PaperWallet(100.0), 25.0)
        self.assertEqual(decision.action, "HOLD")
        self.assertIn("long-only", decision.reason)

    def test_hold_reasons_never_misreport_the_neutral_band(self):
        """A strong reading must never be explained away as 'neutral'."""
        long_wallet = iat.PaperWallet(100.0)
        long_wallet.buy(4, 25.0)
        for score, wallet, price in [
            (0.9, long_wallet, 25.0),                 # bullish, already long
            (0.9, iat.PaperWallet(100.0), 250.0),     # bullish, unaffordable
            (-0.9, iat.PaperWallet(100.0), 25.0),     # bearish, flat
        ]:
            decision = self.strategy.decide(reading(score), wallet, price)
            self.assertEqual(decision.action, "HOLD")
            self.assertNotIn("neutral band", decision.reason)

    def test_bullish_but_unaffordable_holds(self):
        decision = self.strategy.decide(reading(0.9), iat.PaperWallet(100.0), 250.0)
        self.assertEqual(decision.action, "HOLD")
        self.assertIn("cannot cover", decision.reason)


class TestBrokerInterface(unittest.TestCase):
    def setUp(self):
        self.order = iat.Order(symbol="SUZLON", side="BUY", quantity=2, price=48.5)

    def test_paper_broker_accepts_and_ids_the_fill(self):
        result = iat.PaperBroker().place_order(self.order)
        self.assertTrue(result.accepted)
        self.assertTrue(result.order_id.startswith("PAPER-"))

    def test_live_adapters_refuse_to_execute(self):
        for broker in (iat.AngelOneBroker(), iat.FyersBroker()):
            result = broker.place_order(self.order)
            self.assertFalse(result.accepted)
            self.assertIn("disabled", result.message)

    def test_angelone_payload_uses_sdk_field_names(self):
        payload = iat.AngelOneBroker().to_payload(self.order)
        self.assertEqual(payload["tradingsymbol"], "SUZLON-EQ")
        self.assertEqual(payload["transactiontype"], "BUY")
        self.assertEqual(payload["exchange"], "NSE")
        self.assertEqual(payload["quantity"], "2")

    def test_fyers_payload_uses_sdk_field_names(self):
        payload = iat.FyersBroker().to_payload(self.order)
        self.assertEqual(payload["symbol"], "NSE:SUZLON-EQ")
        self.assertEqual(payload["side"], 1)
        self.assertEqual(payload["qty"], 2)


class TestHumanInTheLoop(unittest.TestCase):
    def test_non_interactive_stdin_declines(self):
        """The guardrail that matters: no TTY must never mean 'yes'."""
        order = iat.Order(symbol="SUZLON", side="BUY", quantity=2, price=48.5)
        approved = iat.TradeConfirmation.request(order, iat.PaperWallet(100.0), reading(0.4))
        self.assertFalse(approved)


class TestMarketHours(unittest.TestCase):
    def test_weekday_session_window(self):
        wednesday = datetime(2026, 7, 29, 10, 0, tzinfo=iat.IST)
        self.assertTrue(iat.market_is_open(wednesday))
        self.assertFalse(iat.market_is_open(wednesday.replace(hour=8)))
        self.assertFalse(iat.market_is_open(wednesday.replace(hour=16)))

    def test_weekend_is_closed(self):
        saturday = datetime(2026, 8, 1, 10, 0, tzinfo=iat.IST)
        self.assertFalse(iat.market_is_open(saturday))


class TestSymbolRouting(unittest.TestCase):
    def test_ns_suffix_is_added_once(self):
        for given in ("suzlon", "SUZLON", "SUZLON.NS"):
            feed = iat.MarketDataFeed(given)
            self.assertEqual(feed.symbol, "SUZLON.NS")
            self.assertEqual(feed.base, "SUZLON")


if __name__ == "__main__":
    unittest.main(verbosity=2)
