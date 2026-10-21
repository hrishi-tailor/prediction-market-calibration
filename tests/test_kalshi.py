from pmcal import kalshi


def _market(ticker="T-1", result="yes", volume=5_000.0, close="2026-01-01T00:00:00Z"):
    return {
        "ticker": ticker,
        "event_ticker": "T",
        "series_ticker": "S",
        "category": "Economics",
        "title": "",
        "open_time": "2025-12-01T00:00:00Z",
        "close_time": close,
        "result": result,
        "volume": volume,
        "can_close_early": False,
    }


def test_combo_series_are_excluded():
    assert kalshi.is_combo_series("KXMVECROSSCATEGORY")
    assert not kalshi.is_combo_series("KXFEDDECISION")


def test_eligibility_requires_binary_resolved_and_liquid():
    assert kalshi.is_eligible(_market())
    assert not kalshi.is_eligible(_market(result=""))
    assert not kalshi.is_eligible(_market(volume=10))
    assert not kalshi.is_eligible({**_market(), "market_type": "scalar"})


def test_sampling_is_capped_and_deterministic():
    markets = [_market(ticker=f"T-{i}", close=f"2026-01-{i + 1:02d}T00:00:00Z") for i in range(30)]
    a = kalshi.sample_markets(markets, 10, "S")
    b = kalshi.sample_markets(markets, 10, "S")
    assert len(a) == 10
    assert a == b
    assert [m["close_time"] for m in a] == sorted(m["close_time"] for m in a)
    assert kalshi.sample_markets(markets[:5], 10, "S") == markets[:5]


def test_condense_candles_handles_missing_quotes():
    candles = [
        {
            "end_period_ts": 100,
            "yes_bid": {"close": "0.4000"},
            "yes_ask": {"close": "0.4200"},
            "price": {"close": None},
            "volume": "0.00",
        }
    ]
    assert kalshi.condense_candles(candles) == [[100, 0.4, 0.42, None, 0.0]]
