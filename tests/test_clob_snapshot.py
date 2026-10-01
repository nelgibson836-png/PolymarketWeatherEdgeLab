from clob_snapshot import select_markets


def test_select_markets_prioritizes_low_price_candidates():
    markets = [
        {"market_id":"high", "yes_token":"h", "best_ask":0.90},
        {"market_id":"mid", "yes_token":"m", "best_ask":0.40},
        {"market_id":"low2", "yes_token":"l2", "best_ask":0.08},
        {"market_id":"low1", "yes_token":"l1", "best_ask":0.03},
    ]
    selected = select_markets(markets, max_markets=2)
    assert [m["market_id"] for m in selected] == ["low1", "low2"]


def test_select_markets_fills_capacity_with_remainder():
    markets = [
        {"market_id":"low", "yes_token":"l", "best_ask":0.10},
        {"market_id":"unknown", "yes_token":"u"},
        {"market_id":"high", "yes_token":"h", "best_ask":0.90},
    ]
    selected = select_markets(markets, max_markets=3)
    assert {m["market_id"] for m in selected} == {"low", "unknown", "high"}
