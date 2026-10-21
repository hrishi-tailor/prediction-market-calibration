"""Builds the analysis dataset: one row per (market, horizon).

For each resolved market we look up the price at fixed horizons before it
closed, and pair it with the outcome. On Kalshi the price is the bid/ask
midpoint, because the order book carries forward between candles; on Polymarket
it is the sampled price history, so a point that is too old is dropped.
"""

from __future__ import annotations

import bisect
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from pmcal import config
from pmcal.storage import read_json

log = logging.getLogger(__name__)

# Wider spreads mean the midpoint says little about the market's belief.
MAX_SPREAD = 0.10


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))


def last_at_or_before(timestamps: Sequence[int], t: int) -> int | None:
    """Index of the last timestamp <= t in a sorted sequence, or None."""
    i = bisect.bisect_right(timestamps, t) - 1
    return i if i >= 0 else None


def kalshi_quote_at(rows: Sequence[Sequence[Any]], t: int) -> tuple[float, float] | None:
    """The (bid, ask) in force at time t, from condensed hourly candles."""
    i = last_at_or_before([r[0] for r in rows], t)
    if i is None:
        return None
    bid, ask = rows[i][1], rows[i][2]
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return None
    return bid, ask


def polymarket_price_at(
    points: Sequence[Sequence[float]], t: int, max_age: timedelta
) -> float | None:
    """The last sampled price at or before t, if it is recent enough."""
    i = last_at_or_before([p[0] for p in points], t)
    if i is None or t - points[i][0] > max_age.total_seconds():
        return None
    return float(points[i][1])


def _base_row(platform: str, market: dict[str, Any], close: datetime) -> dict[str, Any]:
    opened = parse_ts(market["open_time"])
    return {
        "platform": platform,
        "market_id": market["market_id"],
        "event_id": market["event_id"],
        "series_id": market["series_id"],
        "category": market["category"],
        "title": market["title"],
        "volume": market["volume"],
        "open_time": opened,
        "close_time": close,
        "duration_days": (close - opened).total_seconds() / 86400,
        "outcome": int(market["outcome"]),
    }


def kalshi_rows(market: dict[str, Any], candles: Sequence[Sequence[Any]]) -> list[dict]:
    close = parse_ts(market["close_time"])
    opened = parse_ts(market["open_time"])
    base = _base_row(
        "Kalshi",
        {
            **market,
            "market_id": market["ticker"],
            "event_id": market["event_ticker"],
            "series_id": market["series_ticker"],
            "outcome": market["result"] == "yes",
        },
        close,
    )
    rows = []
    for name, delta in config.HORIZONS.items():
        t = close - delta
        if t < opened:
            continue
        quote = kalshi_quote_at(candles, int(t.timestamp()))
        if quote is None:
            continue
        bid, ask = quote
        if ask - bid > MAX_SPREAD:
            continue
        rows.append(
            {
                **base,
                "horizon": name,
                "horizon_hours": delta.total_seconds() / 3600,
                "price": (bid + ask) / 2,
                "bid": bid,
                "ask": ask,
            }
        )
    return rows


def polymarket_rows(market: dict[str, Any], points: Sequence[Sequence[float]]) -> list[dict]:
    close = parse_ts(market["close_time"])
    opened = parse_ts(market["open_time"])
    base = _base_row("Polymarket", market, close)
    rows = []
    for name, delta in config.HORIZONS.items():
        t = close - delta
        if t < opened:
            continue
        price = polymarket_price_at(points, int(t.timestamp()), config.STALENESS[name])
        if price is None:
            continue
        rows.append(
            {
                **base,
                "horizon": name,
                "horizon_hours": delta.total_seconds() / 3600,
                "price": price,
                "bid": None,
                "ask": None,
            }
        )
    return rows


def _kalshi_all() -> Iterable[dict]:
    from pmcal import kalshi

    for market in kalshi.load_markets():
        path = kalshi.candle_path(market["ticker"])
        if path.exists():
            yield from kalshi_rows(market, read_json(path))


def _polymarket_all() -> Iterable[dict]:
    from pmcal import polymarket

    for market in polymarket.load_markets():
        path = polymarket.history_path(market["market_id"])
        if path.exists():
            yield from polymarket_rows(market, read_json(path)["history"])


def build() -> pd.DataFrame:
    rows: list[dict] = []
    for platform, source in (("kalshi", _kalshi_all), ("polymarket", _polymarket_all)):
        if (config.RAW_DIR / platform / "markets.json").exists():
            rows.extend(source())
        else:
            log.warning("No %s markets fetched yet; skipping", platform)
    df = pd.DataFrame(rows)
    df["horizon"] = pd.Categorical(df["horizon"], categories=list(config.HORIZONS), ordered=True)
    df.to_parquet(config.DATASET_PATH, index=False)
    log.info(
        "Wrote %d observations from %d markets to %s",
        len(df),
        df["market_id"].nunique(),
        config.DATASET_PATH,
    )
    return df


def load() -> pd.DataFrame:
    return pd.read_parquet(config.DATASET_PATH)
