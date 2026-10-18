import unittest
from unittest.mock import Mock, patch

from clob_snapshot import LEVELS, fetch_book, normalize_levels, select_markets


class ClobSnapshotTests(unittest.TestCase):
    def test_select_markets_prioritizes_low_price_candidates(self):
        markets = [
            {"market_id": "high", "yes_token": "h", "best_ask": 0.90},
            {"market_id": "mid", "yes_token": "m", "best_ask": 0.40},
            {"market_id": "low2", "yes_token": "l2", "best_ask": 0.08},
            {"market_id": "low1", "yes_token": "l1", "best_ask": 0.03},
        ]
        selected = select_markets(markets, max_markets=2)
        self.assertEqual([m["market_id"] for m in selected], ["low1", "low2"])

    def test_select_markets_fills_capacity_with_remainder(self):
        markets = [
            {"market_id": "low", "yes_token": "l", "best_ask": 0.10},
            {"market_id": "unknown", "yes_token": "u"},
            {"market_id": "high", "yes_token": "h", "best_ask": 0.90},
        ]
        selected = select_markets(markets, max_markets=3)
        self.assertEqual(
            {m["market_id"] for m in selected}, {"low", "unknown", "high"}
        )

    def test_signal_markets_are_selected_first(self):
        markets = [
            {"market_id": "cheap", "yes_token": "c", "best_ask": 0.03},
            {"market_id": "signal", "yes_token": "s", "best_ask": 0.90},
        ]
        selected = select_markets(
            markets, max_markets=1, priority_market_ids=["signal"]
        )
        self.assertEqual([m["market_id"] for m in selected], ["signal"])

    def test_signal_priority_skips_inactive_ids_and_deduplicates(self):
        markets = [
            {"market_id": "active", "yes_token": "a", "best_ask": 0.90},
            {"market_id": "cheap", "yes_token": "c", "best_ask": 0.03},
        ]
        selected = select_markets(
            markets,
            max_markets=2,
            priority_market_ids=["missing", "active", "active"],
        )
        self.assertEqual(
            [m["market_id"] for m in selected], ["active", "cheap"]
        )

    def test_normalize_levels_sorts_and_filters_invalid_values(self):
        levels = [
            {"price": "0.999", "size": "1"},
            {"price": "0.770", "size": "11"},
            {"price": "nan", "size": "1"},
            {"price": "0.500", "size": "0"},
            {"price": "-0.1", "size": "3"},
            {"price": "0.2", "size": "inf"},
            {"price": "1.0", "size": "2"},
            {"price": "bad", "size": "4"},
        ]
        normalized = normalize_levels(levels)
        self.assertEqual(
            [float(x["price"]) for x in normalized], [0.770, 0.999, 1.0]
        )
        bids = normalize_levels(levels, descending=True)
        self.assertEqual(
            [float(x["price"]) for x in bids], [1.0, 0.999, 0.770]
        )

    def test_fetch_book_sorts_before_truncating(self):
        asks = [
            {"price": f"{price:.3f}", "size": str(index + 1)}
            for index, price in enumerate(
                [
                    0.999, 0.998, 0.997, 0.996, 0.995,
                    0.994, 0.993, 0.992, 0.991, 0.990, 0.770,
                ]
            )
        ]
        bids = [
            {"price": "0.100", "size": "1"},
            {"price": "0.120", "size": "2"},
            {"price": "0.150", "size": "3"},
            {"price": "0.200", "size": "4"},
        ]
        response = Mock()
        response.json.return_value = {"bids": bids, "asks": asks}
        response.raise_for_status.return_value = None

        with patch("clob_snapshot.requests.get", return_value=response):
            market_id, book, error = fetch_book(
                {"market_id": "test-market", "yes_token": "test-token"}
            )

        self.assertIsNone(error)
        self.assertEqual(market_id, "test-market")
        self.assertEqual(book["best_ask"], 0.770)
        self.assertEqual(book["best_bid"], 0.200)
        self.assertEqual(len(book["asks"]), LEVELS)
        self.assertEqual(book["depth_ask_at_best"], 11.0)

    def test_fetch_book_handles_empty_sides(self):
        response = Mock()
        response.json.return_value = {"bids": [], "asks": []}
        response.raise_for_status.return_value = None

        with patch("clob_snapshot.requests.get", return_value=response):
            _, book, error = fetch_book(
                {"market_id": "empty", "yes_token": "empty-token"}
            )

        self.assertIsNone(error)
        self.assertEqual(book["status"], "EMPTY")
        self.assertIsNone(book["best_bid"])
        self.assertIsNone(book["best_ask"])


if __name__ == "__main__":
    unittest.main()
