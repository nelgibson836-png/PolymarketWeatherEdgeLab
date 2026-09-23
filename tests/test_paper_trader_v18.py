import sys
sys.path.insert(0, ".")

from paper_trader_v18 import (
    VIRTUAL_BANKROLL,
    MAX_OPEN_TRADES,
    decision_key,
    signal_is_fresh,
    build_trade,
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
    test_trade_rejected_without_verified_clob_source()
    test_signal_age_rejects_stale_data()
    print("v18 paper trader tests: PASS")
