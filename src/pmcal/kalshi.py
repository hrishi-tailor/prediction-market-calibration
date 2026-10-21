"""Fetches settled Kalshi markets and their hourly price history.

Markets are discovered series by series, because the flat market listing is
dominated by multivariate combo markets and a handful of hourly series. Each
series contributes at most `KALSHI_MAX_MARKETS_PER_SERIES` randomly sampled
markets, and each market's hourly candles cover the 31 days before it closed.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tqdm import tqdm

from pmcal import config
from pmcal.http import ApiClient
from pmcal.storage import read_json, safe_name, write_json

log = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
RAW = config.RAW_DIR / "kalshi"
PAGE_SIZE = 1000
CANDLE_WINDOW = timedelta(days=31)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_combo_series(ticker: str) -> bool:
    """Multivariate combo ('parlay') markets are bundles of other markets."""
    return ticker.startswith("KXMVE")


def fetch_series(client: ApiClient) -> list[dict[str, Any]]:
    path = RAW / "series.json"
    if path.exists():
        return read_json(path)
    series = [
        {"ticker": s["ticker"], "category": s.get("category") or "Unknown", "title": s["title"]}
        for s in client.get("/series")["series"]
        if not is_combo_series(s["ticker"])
    ]
    write_json(path, series)
    return series


def _paginate(client: ApiClient, path: str, params: dict[str, Any], limit: int) -> Iterator[dict]:
    cursor = None
    seen = 0
    while seen < limit:
        page = client.get(
            path, {**params, "limit": PAGE_SIZE, **({"cursor": cursor} if cursor else {})}
        )
        if not page:
            return
        markets = page.get("markets") or []
        yield from markets
        seen += len(markets)
        cursor = page.get("cursor")
        if not cursor or not markets:
            return


def slim_market(market: dict[str, Any], series: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": market["ticker"],
        "event_ticker": market["event_ticker"],
        "series_ticker": series["ticker"],
        "category": series["category"],
        "title": market.get("title", ""),
        "open_time": market["open_time"],
        "close_time": market["close_time"],
        "result": market.get("result", ""),
        "volume": float(market.get("volume_fp") or market.get("volume") or 0),
        "can_close_early": bool(market.get("can_close_early")),
    }


def is_eligible(market: dict[str, Any]) -> bool:
    return (
        market.get("market_type", "binary") == "binary"
        and market["result"] in ("yes", "no")
        and market["volume"] >= config.KALSHI_MIN_VOLUME
    )


def sample_markets(markets: list[dict[str, Any]], cap: int, seed_key: str) -> list[dict[str, Any]]:
    """Deterministically samples at most `cap` markets, seeded per series."""
    if len(markets) <= cap:
        return markets
    rng = random.Random(f"{config.RANDOM_SEED}:{seed_key}")
    return sorted(rng.sample(markets, cap), key=lambda m: m["close_time"])


def list_series_markets(client: ApiClient, series: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the sampled, eligible settled markets of one series (cached)."""
    path = RAW / "markets" / f"{safe_name(series['ticker'])}.json"
    if path.exists():
        return read_json(path)
    raw: dict[str, dict] = {}
    params = {"series_ticker": series["ticker"]}
    limit = config.KALSHI_MAX_LISTED_PER_SERIES
    # Recently settled markets live under /markets; older ones were moved to /historical.
    for m in _paginate(client, "/markets", {**params, "status": "settled"}, limit):
        raw[m["ticker"]] = m
    for m in _paginate(client, "/historical/markets", params, limit):
        raw.setdefault(m["ticker"], m)
    eligible = []
    for m in raw.values():
        slim = slim_market(m, series)
        if is_eligible({**slim, "market_type": m.get("market_type", "binary")}):
            eligible.append(slim)
    eligible.sort(key=lambda m: m["close_time"])
    sampled = sample_markets(eligible, config.KALSHI_MAX_MARKETS_PER_SERIES, series["ticker"])
    write_json(path, sampled)
    return sampled


def _dollars(block: dict[str, Any] | None, key: str = "close") -> float | None:
    if not block or block.get(key) in (None, ""):
        return None
    return float(block[key])


def condense_candles(candles: list[dict[str, Any]]) -> list[list[Any]]:
    """Keeps [end_ts, yes_bid, yes_ask, last_price, volume] for each hourly candle."""
    rows = []
    for c in candles:
        rows.append(
            [
                c["end_period_ts"],
                _dollars(c.get("yes_bid")),
                _dollars(c.get("yes_ask")),
                _dollars(c.get("price")),
                float(c.get("volume") or 0),
            ]
        )
    return rows


def candle_path(ticker: str) -> Path:
    return RAW / "candles" / f"{safe_name(ticker)}.json.gz"


def fetch_candles(client: ApiClient, market: dict[str, Any]) -> None:
    path = candle_path(market["ticker"])
    if path.exists():
        return
    close = parse_ts(market["close_time"])
    start = max(parse_ts(market["open_time"]), close - CANDLE_WINDOW)
    params = {
        "start_ts": int(start.timestamp()),
        "end_ts": int(close.timestamp()),
        "period_interval": 60,
    }
    live = f"/series/{market['series_ticker']}/markets/{market['ticker']}/candlesticks"
    historical = f"/historical/markets/{market['ticker']}/candlesticks"
    body = client.get(historical, params) or client.get(live, params)
    candles = (body or {}).get("candlesticks") or []
    write_json(path, condense_candles(candles))


def run(rate: float = 12.0, workers: int = 8) -> None:
    with ApiClient(BASE_URL, rate=rate) as client:
        series = fetch_series(client)
        log.info("Kalshi: %d series (combo series excluded)", len(series))
        markets: list[dict[str, Any]] = []
        with ThreadPoolExecutor(workers) as pool:
            for batch in tqdm(
                pool.map(lambda s: list_series_markets(client, s), series),
                total=len(series),
                desc="kalshi markets",
            ):
                markets.extend(batch)
        log.info("Kalshi: %d sampled markets", len(markets))
        with ThreadPoolExecutor(workers) as pool:
            list(
                tqdm(
                    pool.map(lambda m: fetch_candles(client, m), markets),
                    total=len(markets),
                    desc="kalshi candles",
                )
            )


def load_markets() -> list[dict[str, Any]]:
    return [m for p in sorted((RAW / "markets").glob("*.json")) for m in read_json(p)]
