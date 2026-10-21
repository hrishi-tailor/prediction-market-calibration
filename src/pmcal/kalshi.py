"""Fetches settled Kalshi markets and their hourly price history.

Discovery is one sequential scan over every settled market (the historical
endpoint only supports cursor paging, and per-series listing is heavily rate
limited). Combo markets are excluded server-side; thin and unresolved markets are
dropped as each page arrives. Each series then contributes at most
`KALSHI_MAX_MARKETS_PER_SERIES` randomly sampled markets, so a few hourly series
cannot dominate, and each sampled market's hourly candles cover the 31 days
before it closed.
"""

from __future__ import annotations

import logging
import random
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
# Markets settled before this moved to the /historical endpoints (GET /historical/cutoff).
HISTORICAL_CUTOFF = datetime.fromisoformat("2026-08-22T00:00:00+00:00")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_combo_series(ticker: str) -> bool:
    """Multivariate combo ('parlay') markets are bundles of other markets."""
    return ticker.startswith("KXMVE")


def series_of(event_ticker: str) -> str:
    """Kalshi event tickers are '<SERIES>-<suffix>'."""
    return event_ticker.split("-", 1)[0]


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


def scan_settled(client: ApiClient, path: str, params: dict[str, Any], name: str) -> None:
    """Pages through every settled market at `path`, keeping only eligible ones.

    Each page's survivors go to their own chunk file and the cursor is
    checkpointed after every page, so an interrupted scan resumes where it stopped.
    """
    series = {s["ticker"]: s for s in fetch_series(client)}
    out_dir = RAW / "scan" / name
    state_path = out_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else {"page": 0, "cursor": None}
    if state.get("done"):
        return
    progress = tqdm(desc=f"kalshi scan {name}", initial=state["page"], unit="page")
    while not state.get("done"):
        query = {**params, "mve_filter": "exclude", "limit": PAGE_SIZE}
        if state["cursor"]:
            query["cursor"] = state["cursor"]
        page = client.get(path, query) or {}
        markets = page.get("markets") or []
        kept = []
        for m in markets:
            s = series.get(series_of(m["event_ticker"]))
            if s is None:
                continue
            slim = slim_market(m, s)
            if is_eligible({**slim, "market_type": m.get("market_type", "binary")}):
                kept.append(slim)
        write_json(out_dir / f"page-{state['page']:06d}.json", kept)
        cursor = page.get("cursor")
        state = {"page": state["page"] + 1, "cursor": cursor, "done": not cursor or not markets}
        write_json(state_path, state)
        progress.update()
    progress.close()


def select_markets() -> list[dict[str, Any]]:
    """Dedupes scanned markets and samples at most N per series."""
    by_ticker: dict[str, dict] = {}
    for chunk in sorted((RAW / "scan").glob("*/page-*.json")):
        for m in read_json(chunk):
            by_ticker.setdefault(m["ticker"], m)
    by_series: dict[str, list] = {}
    for m in by_ticker.values():
        by_series.setdefault(m["series_ticker"], []).append(m)
    selected = []
    for key in sorted(by_series):
        markets = sorted(by_series[key], key=lambda m: m["close_time"])
        selected.extend(sample_markets(markets, config.KALSHI_MAX_MARKETS_PER_SERIES, key))
    log.info(
        "Kalshi: %d eligible markets in %d series; %d sampled",
        len(by_ticker),
        len(by_series),
        len(selected),
    )
    write_json(RAW / "markets.json", selected)
    return selected


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
    live = f"/series/{market['series_ticker']}/markets/{market['ticker']}/candlesticks"
    historical = f"/historical/markets/{market['ticker']}/candlesticks"
    # Markets settled before Kalshi's historical cutoff only exist on /historical.
    first, second = (historical, live) if close < HISTORICAL_CUTOFF else (live, historical)
    body = client.get(first, params) or client.get(second, params)
    write_json(path, condense_candles((body or {}).get("candlesticks") or []))


def run(rate: float = 4.0, workers: int = 4) -> None:
    with ApiClient(BASE_URL, rate=rate) as client:
        scan_settled(client, "/historical/markets", {}, "historical")
        scan_settled(client, "/markets", {"status": "settled"}, "live")
        markets = select_markets()
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
