from datetime import UTC, datetime, timedelta

from pmcal import dataset

CLOSE = datetime(2026, 1, 31, tzinfo=UTC)


def ts(dt: datetime) -> int:
    return int(dt.timestamp())


def kalshi_market(**overrides):
    market = {
        "ticker": "FED-26JAN-H0",
        "event_ticker": "FED-26JAN",
        "series_ticker": "FED",
        "category": "Economics",
        "title": "Fed holds?",
        "open_time": "2025-12-01T00:00:00Z",
        "close_time": "2026-01-31T00:00:00Z",
        "result": "yes",
        "volume": 50_000.0,
    }
    return {**market, **overrides}


def test_last_at_or_before():
    assert dataset.last_at_or_before([10, 20, 30], 5) is None
    assert dataset.last_at_or_before([10, 20, 30], 20) == 1
    assert dataset.last_at_or_before([10, 20, 30], 25) == 1
    assert dataset.last_at_or_before([10, 20, 30], 99) == 2


def test_kalshi_uses_midpoint_of_quote_in_force():
    candles = [
        [ts(CLOSE - timedelta(days=40)), 0.30, 0.34, 0.32, 10],
        [ts(CLOSE - timedelta(days=3)), 0.60, 0.62, 0.61, 10],
        [ts(CLOSE - timedelta(minutes=30)), 0.97, 0.99, 0.98, 10],
    ]
    rows = {r["horizon"]: r for r in dataset.kalshi_rows(kalshi_market(), candles)}
    # The quote set 40 days out is still in force 30 and 7 days out.
    assert rows["30d"]["price"] == 0.32
    assert rows["7d"]["price"] == 0.32
    assert rows["1d"]["price"] == 0.61
    # The 30-minute-before-close quote must not leak into the 1-hour horizon.
    assert rows["1h"]["price"] == 0.61
    assert all(r["outcome"] == 1 for r in rows.values())


def test_kalshi_skips_wide_spreads_and_horizons_before_open():
    candles = [[ts(CLOSE - timedelta(days=5)), 0.10, 0.90, None, 0]]
    market = kalshi_market(open_time="2026-01-20T00:00:00Z", result="no")
    assert dataset.kalshi_rows(market, candles) == []


def test_polymarket_drops_stale_points():
    market = {
        "market_id": "1",
        "event_id": "e",
        "series_id": "e",
        "category": "Politics",
        "title": "x",
        "volume": 1e5,
        "open_time": "2025-12-01T00:00:00Z",
        "close_time": "2026-01-31T00:00:00Z",
        "outcome": 0,
    }
    points = [
        [ts(CLOSE - timedelta(days=31)), 0.2],
        [ts(CLOSE - timedelta(hours=1, minutes=5)), 0.1],
    ]
    rows = {r["horizon"]: r for r in dataset.polymarket_rows(market, points)}
    assert rows["30d"]["price"] == 0.2
    assert "7d" not in rows  # last point is 24 days old, beyond the 1-day tolerance
    assert rows["1h"]["price"] == 0.1


def test_closed_early():
    assert dataset.closed_early({"end": "2025-03-10T00:00:00Z", "closed": "2025-03-01 12:00:00+00"})
    assert not dataset.closed_early(
        {"end": "2025-03-10T00:00:00Z", "closed": "2025-03-09 12:00:00+00"}
    )
    assert dataset.closed_early({"end": None, "closed": "2025-03-01 12:00:00+00"}) is None
    assert dataset.closed_early(None) is None


def test_kalshi_volume_before_counts_only_the_trailing_window():
    rows = [
        [ts(CLOSE - timedelta(days=3)), 0.5, 0.52, 0.51, 100],
        [ts(CLOSE - timedelta(hours=30)), 0.5, 0.52, 0.51, 7],
        [ts(CLOSE - timedelta(hours=20)), 0.5, 0.52, 0.51, 40],
        [ts(CLOSE - timedelta(hours=1)), 0.5, 0.52, 0.51, 3],
    ]
    t = ts(CLOSE - timedelta(hours=1))
    # 24h window ending at t covers the candles 20h and 1h before close, not the 30h one.
    assert dataset.kalshi_volume_before(rows, t, timedelta(hours=24)) == 43


def test_kalshi_rows_carry_recent_volume():
    candles = [
        [ts(CLOSE - timedelta(days=2)), 0.4, 0.42, 0.41, 50],
        [ts(CLOSE - timedelta(hours=30)), 0.4, 0.42, 0.41, 5],
    ]
    rows = {r["horizon"]: r for r in dataset.kalshi_rows(kalshi_market(), candles)}
    assert rows["1d"]["volume_24h"] == 5
    assert rows["1h"]["volume_24h"] == 0


def valid_frame():
    import pandas as pd

    return pd.DataFrame(
        {
            "platform": ["Kalshi", "Polymarket"],
            "market_id": ["a", "b"],
            "event_id": ["e", "f"],
            "category": ["Economics", "Sports"],
            "volume": [1000.0, 20000.0],
            "open_time": [CLOSE - timedelta(days=40)] * 2,
            "close_time": [CLOSE] * 2,
            "outcome": [1, 0],
            "horizon": ["1d", "1h"],
            "price": [0.6, 0.2],
            "bid": [0.59, None],
            "ask": [0.61, None],
            "volume_24h": [12.0, None],
        }
    )


def test_validate_accepts_a_clean_table():
    df = valid_frame()
    assert dataset.validate(df) is df


def test_validate_lists_every_broken_invariant():
    import pytest

    df = valid_frame()
    df.loc[0, "price"] = 1.4
    df.loc[1, "outcome"] = 2
    df.loc[0, "bid"] = 0.9
    with pytest.raises(dataset.DatasetError) as err:
        dataset.validate(df)
    message = str(err.value)
    assert "price outside [0, 1]" in message
    assert "outcome not 0 or 1" in message
    assert "bid above ask" in message


def test_validate_rejects_missing_columns():
    import pytest

    with pytest.raises(dataset.DatasetError, match="missing columns"):
        dataset.validate(valid_frame().drop(columns=["outcome"]))


def test_polymarket_rejects_prices_outside_zero_one():
    t = ts(CLOSE)
    assert dataset.polymarket_price_at([[t - 60, 1.005]], t, timedelta(hours=2)) is None
    assert dataset.polymarket_price_at([[t - 60, 0.995]], t, timedelta(hours=2)) == 0.995
