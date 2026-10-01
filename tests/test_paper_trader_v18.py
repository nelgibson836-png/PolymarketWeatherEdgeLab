import sys
sys.path.insert(0, ".")

from paper_trader_v18 import (
    VIRTUAL_BANKROLL,
    MAX_OPEN_TRADES,
    decision_key,
    signal_is_fresh,
    build_trade,
    settle_trade,
    simulate_clob_fill,
)


def _signal():
    return {
        "market_id": "1",
        "entry_price": 0.20,
        "raw_model_probability": 0.40,
        "shrunken_model_probability": 0.35,
        "fee_per_share": 0.01,
        "run_at": "2026-09-22T12:00:00+00:00",
        "signal": "PAPER_BUY_V18",
        "execution_source": "clob_book",
    }


def test_decision_key_is_stable():
    s = {
        "market_id": "1",
        "market_date": "2026-09-22",
        "bucket_type": "exact",
        "bucket_value": "30",
        "run_at": "2026-09-22T12:00:00+00:00",
        "signal": "PAPER_BUY_V18",
    }
    assert decision_key(s) == decision_key(dict(s))


def test_trade_budget_includes_fee():
    t = build_trade(_signal(), 1, 10.0)
    assert t is not None
    assert t["stake"] <= 10.0 + 1e-9
    assert t["fee_total"] > 0



def test_settlement_does_not_charge_entry_fee_twice():
    gross_payout, net_pnl = settle_trade(10.0, 1.0, 10.50)
    assert gross_payout == 10.0
    assert net_pnl == -0.50


def test_winning_settlement_pays_gross_payout_in_full():
    gross_payout, net_pnl = settle_trade(20.0, 1.0, 9.0)
    assert gross_payout == 20.0
    assert net_pnl == 11.0


def test_clob_fill_walks_multiple_ask_levels():
    book = {
        "asks": [
            {"price": "0.10", "size": "50"},
            {"price": "0.20", "size": "100"},
        ]
    }
    fill = simulate_clob_fill(book, 10.0, fee_rate=0.05)
    assert fill is not None
    assert fill["shares"] > 50
    assert fill["execution_price"] > 0.10
    assert fill["execution_price"] < 0.20
    assert fill["fee_total"] > 0


def test_clob_fill_rejects_empty_book():
    assert simulate_clob_fill({"asks": []}, 10.0, fee_rate=0.05) is None

def test_no_trade_without_cash():
    assert build_trade(_signal(), 1, 0.0) is None


def test_trade_rejected_without_verified_clob_source():
    s = _signal()
    s["execution_source"] = "legacy_quote"
    assert build_trade(s, 1, 10.0) is None


def test_signal_age_rejects_stale_data():
    stale = {"run_at": "2020-01-01T00:00:00+00:00"}
    assert signal_is_fresh(stale) is False


if __name__ == "__main__":
    test_decision_key_is_stable()
    test_trade_budget_includes_fee()
    test_no_trade_without_cash()
    test_settlement_does_not_charge_entry_fee_twice()
    test_winning_settlement_pays_gross_payout_in_full()
    test_clob_fill_walks_multiple_ask_levels()
    test_clob_fill_rejects_empty_book()
    test_trade_rejected_without_verified_clob_source()
    test_signal_age_rejects_stale_data()
    print("v18 paper trader tests: PASS")
