from edge_engine_v18 import consensus_probability


def test_consensus_probability_averages_available_models():
    market = {
        "bucket_type": "exact",
        "bucket_value": 20,
        "bucket_low": 20,
        "bucket_high": 20,
    }
    stats = {
        "entries": [
            {"forecast": 20.0, "errors": [0.0, 0.0, 0.0]},
            {"forecast": 19.0, "errors": [1.0, 1.0, 1.0]},
        ]
    }
    probability, count = consensus_probability(market, stats)
    assert count == 2
    assert probability == 0.5


def test_consensus_probability_uses_single_model_when_only_one_is_available():
    market = {
        "bucket_type": "exact",
        "bucket_value": 20,
        "bucket_low": 20,
        "bucket_high": 20,
    }
    stats = {
        "entries": [
            {"forecast": 20.0, "errors": [0.0, 0.0, 0.0]},
        ]
    }
    probability, count = consensus_probability(market, stats)
    assert count == 1
    assert probability == 0.875
