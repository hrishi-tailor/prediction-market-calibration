"""Fetches settled Kalshi markets and their hourly price history.

Markets are discovered series by series. A flat scan of every settled market is
not practical: Kalshi lists ~75k markets a day, mostly hourly and 15-minute
series. Series with less than `KALSHI_MIN_VOLUME` lifetime volume and combo
series are skipped up front. For each remaining series we read the most recent
page (up to 1,000 markets) from both the live and historical endpoints, drop
thin and unresolved markets, and keep at most `KALSHI_MAX_MARKETS_PER_SERIES`
randomly sampled ones, so a few hourly series cannot dominate. Each sampled
market's hourly candles cover the 31 days before it closed.
"""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from tqdm import tqdm

from pmcal import config
from pmcal.http import ApiClient
from pmcal.storage import read_json, safe_name, write_json

log = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
RAW = config.RAW_DIR / "kalshi"
PAGE_SIZE = 1000
CANDLE_WINDOW = timedelta(days=31)
# Markets settled before this moved to the /historical endpoints (GET /historical/cutoff).
HISTORICAL_CUTOFF = datetime.fromisoformat("2026-08-22T00:00:00+00:00")


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
        {
            "ticker": s["ticker"],
            "category": s.get("category") or "Unknown",
            "title": s["title"],
            "volume": float(s.get("volume_fp") or 0),
        }
        for s in client.get("/series", {"include_volume": "true"})["series"]
        if not is_combo_series(s["ticker"])
    ]
    write_json(path, series)
    return series


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
    """The sampled, eligible settled markets of one series (cached per series)."""
    path = RAW / "markets" / f"{safe_name(series['ticker'])}.json"
    if path.exists():
        return read_json(path)
    raw: dict[str, dict] = {}
    params = {"series_ticker": series["ticker"], "limit": PAGE_SIZE}
    # Recently settled markets live under /markets; older ones moved to /historical.
    for endpoint, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        page = client.get(endpoint, {**params, **extra}) or {}
        for m in page.get("markets") or []:
            raw.setdefault(m["ticker"], m)
    eligible = [
        slim
        for m in raw.values()
        if is_eligible(
            {**(slim := slim_market(m, series)), "market_type": m.get("market_type", "binary")}
        )
    ]
    eligible.sort(key=lambda m: m["close_time"])
    sampled = sample_markets(eligible, config.KALSHI_MAX_LISTED_PER_SERIES, series["ticker"])
    write_json(path, sampled)
    return sampled


def select_markets(listed: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Caps each series, then the pooled sample, with seeded random draws."""
    markets = [
        m
        for batch in listed
        if batch
        for m in sample_markets(
            batch, config.KALSHI_MAX_MARKETS_PER_SERIES, batch[0]["series_ticker"]
        )
    ]
    eligible = len(markets)
    markets = sample_markets(markets, config.KALSHI_MAX_MARKETS, "all")
    log.info("Kalshi: %d markets after the per-series cap; %d sampled", eligible, len(markets))
    return sorted(markets, key=lambda m: m["close_time"])


def _dollars(block: dict[str, Any] | None, key: str = "close") -> float | None:
    if not block or block.get(key) in (None, ""):
        return None
    return float(block[key])


def condense_candles(candles: list[dict[str, Any]]) -> list[list[Any]]:
    """Keeps [end_ts, yes_bid, yes_ask, last_price, volume] for each hourly candle."""
    return [
        [
            c["end_period_ts"],
            _dollars(c.get("yes_bid")),
            _dollars(c.get("yes_ask")),
            _dollars(c.get("price")),
            float(c.get("volume") or 0),
        ]
        for c in candles
    ]


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
    # A few old tickers contain characters such as '%' that must be escaped in a path.
    ticker, series = quote(market["ticker"], safe=""), quote(market["series_ticker"], safe="")
    live = f"/series/{series}/markets/{ticker}/candlesticks"
    historical = f"/historical/markets/{ticker}/candlesticks"
    # Markets settled before Kalshi's historical cutoff only exist on /historical.
    first, second = (historical, live) if close < HISTORICAL_CUTOFF else (live, historical)
    try:
        body = client.get(first, params) or client.get(second, params)
    except httpx.HTTPStatusError as exc:
        # One malformed market should not stop a multi-hour fetch; record it as empty.
        log.warning("No candles for %s: %s", market["ticker"], exc.response.status_code)
        body = None
    write_json(path, condense_candles((body or {}).get("candlesticks") or []))


def run(rate: float = 4.0, workers: int = 4) -> None:
    with ApiClient(BASE_URL, rate=rate) as client:
        series = [s for s in fetch_series(client) if s["volume"] >= config.KALSHI_MIN_SERIES_VOLUME]
        log.info("Kalshi: %d series with enough volume", len(series))
        with ThreadPoolExecutor(workers) as pool:
            listed = list(
                tqdm(
                    pool.map(lambda s: list_series_markets(client, s), series),
                    total=len(series),
                    desc="kalshi series",
                )
            )
        markets = select_markets(listed)
        write_json(RAW / "markets.json", markets)
        with ThreadPoolExecutor(workers) as pool:
            list(
                tqdm(
                    pool.map(lambda m: fetch_candles(client, m), markets),
                    total=len(markets),
                    desc="kalshi candles",
                )
            )


def load_markets() -> list[dict[str, Any]]:
    return read_json(RAW / "markets.json")
