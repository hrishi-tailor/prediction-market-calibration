"""Fetches resolved Polymarket markets and their price history.

Events are discovered week by week from the Gamma API (offsets are capped, so
short windows keep each listing small). Only plain Yes/No markets that resolved
cleanly to 1 or 0 with enough volume are kept, at most
`POLYMARKET_MAX_MARKETS_PER_EVENT` per event. Prices come from the CLOB
`prices-history` endpoint for the Yes token. Hourly history is not available for
older markets, so the fetcher falls back to 12-hourly and then daily points; the
dataset builder drops any point too old for its horizon.
"""

from __future__ import annotations

import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tqdm import tqdm

from pmcal import config
from pmcal.http import ApiClient
from pmcal.storage import read_json, safe_name, write_json

log = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
RAW = config.RAW_DIR / "polymarket"
PAGE_SIZE = 100
FIRST_WEEK = datetime(2020, 10, 5, tzinfo=UTC)
FIDELITIES = (60, 720, 1440)  # minutes; finest first

# Gamma tags are free-form; map them to a small set of categories, in priority order.
CATEGORY_TAGS: list[tuple[str, set[str]]] = [
    ("Sports", {"sports", "nba", "nfl", "mlb", "nhl", "soccer", "tennis", "cbb", "cfb", "esports"}),
    ("Crypto", {"crypto", "bitcoin", "ethereum", "solana", "crypto-prices"}),
    ("Economics", {"economy", "finance", "business", "fed", "fed-rates", "stocks", "macro"}),
    ("Politics", {"politics", "elections", "us-politics", "trump", "global-elections"}),
    ("World", {"world", "geopolitics", "middle-east", "ukraine"}),
    ("Culture", {"pop-culture", "culture", "movies", "music", "awards", "entertainment"}),
    ("Science & Tech", {"science", "tech", "ai", "space"}),
    ("Weather", {"weather", "climate"}),
]


def categorize(tag_slugs: set[str]) -> str:
    for category, slugs in CATEGORY_TAGS:
        if tag_slugs & slugs:
            return category
    return "Other"


def _parse_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        return json.loads(value or "[]")
    except json.JSONDecodeError:
        return []


def resolved_outcome(market: dict[str, Any]) -> int | None:
    """1 if Yes won, 0 if No won, None for anything else (unresolved, 50/50, multi-outcome)."""
    if _parse_list(market.get("outcomes")) != ["Yes", "No"]:
        return None
    status = market.get("umaResolutionStatus")
    if status is not None and status != "resolved":
        return None
    prices = [float(p) for p in _parse_list(market.get("outcomePrices"))]
    if len(prices) != 2:
        return None
    if prices[0] >= 0.99 and prices[1] <= 0.01:
        return 1
    if prices[0] <= 0.01 and prices[1] >= 0.99:
        return 0
    return None


def _earliest(*values: str | None) -> str | None:
    parsed = [
        datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T")) for v in values if v
    ]
    parsed = [p if p.tzinfo else p.replace(tzinfo=UTC) for p in parsed]
    return min(parsed).isoformat() if parsed else None


def slim_market(market: dict[str, Any], event: dict[str, Any], category: str) -> dict | None:
    outcome = resolved_outcome(market)
    tokens = _parse_list(market.get("clobTokenIds"))
    # Markets can be resolved well after trading effectively ends, and some list an
    # end date after they actually closed, so the earlier of the two is the close.
    close = _earliest(market.get("endDate"), market.get("closedTime"))
    opened = market.get("startDate") or market.get("createdAt")
    if outcome is None or len(tokens) != 2 or not close or not opened:
        return None
    volume = float(market.get("volumeNum") or market.get("volume") or 0)
    if volume < config.POLYMARKET_MIN_VOLUME:
        return None
    return {
        "market_id": str(market["id"]),
        "event_id": str(event["id"]),
        "series_id": event.get("seriesSlug") or str(event["id"]),
        "category": category,
        "title": market.get("question", ""),
        "open_time": opened,
        "close_time": close,
        "outcome": outcome,
        "volume": volume,
        "yes_token": tokens[0],
    }


def event_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    category = categorize({t.get("slug", "") for t in event.get("tags") or []})
    markets = [
        m for m in (slim_market(m, event, category) for m in event.get("markets") or []) if m
    ]
    if len(markets) > config.POLYMARKET_MAX_MARKETS_PER_EVENT:
        rng = random.Random(f"{config.RANDOM_SEED}:{event['id']}")
        markets = rng.sample(markets, config.POLYMARKET_MAX_MARKETS_PER_EVENT)
    return markets


# Gamma rejects offsets past roughly 2,000, so a busy window is split in half.
MAX_OFFSET = 2_000


class WindowTooLarge(Exception):
    pass


def _list_window(client: ApiClient, start: datetime, end: datetime) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    offset = 0
    while True:
        if offset >= MAX_OFFSET:
            raise WindowTooLarge
        page = client.get(
            "/events",
            {
                "closed": "true",
                "end_date_min": start.isoformat().replace("+00:00", "Z"),
                "end_date_max": end.isoformat().replace("+00:00", "Z"),
                # An event's volume is at least that of any of its markets.
                "volume_min": config.POLYMARKET_MIN_VOLUME,
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        )
        if not page:
            return events
        events.extend(page)
        if len(page) < PAGE_SIZE:
            return events
        offset += PAGE_SIZE


def list_events(client: ApiClient, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """All closed events ending in [start, end), splitting the window when it is too busy."""
    try:
        return _list_window(client, start, end)
    except WindowTooLarge:
        mid = start + (end - start) / 2
        return list_events(client, start, mid) + list_events(client, mid, end)


def fetch_week(client: ApiClient, start: datetime) -> list[dict[str, Any]]:
    path = RAW / "weeks" / f"{start:%Y-%m-%d}.json"
    if path.exists():
        return read_json(path)
    markets: list[dict[str, Any]] = []
    for event in list_events(client, start, start + timedelta(days=7)):
        markets.extend(event_markets(event))
    write_json(path, markets)
    return markets


def history_path(market_id: str) -> Path:
    return RAW / "history" / f"{safe_name(market_id)}.json.gz"


def fetch_history(client: ApiClient, market: dict[str, Any]) -> None:
    path = history_path(market["market_id"])
    if path.exists():
        return
    history: list = []
    fidelity = None
    for fidelity in FIDELITIES:
        body = client.get(
            "/prices-history",
            {"market": market["yes_token"], "interval": "max", "fidelity": fidelity},
        )
        history = (body or {}).get("history") or []
        if len(history) >= 2:
            break
    write_json(
        path,
        {"fidelity": fidelity, "history": sorted([[int(p["t"]), float(p["p"])] for p in history])},
    )


def weeks(until: datetime) -> list[datetime]:
    out, week = [], FIRST_WEEK
    while week < until:
        out.append(week)
        week += timedelta(days=7)
    return out


def run(rate: float = 8.0, workers: int = 6) -> None:
    now = datetime.now(UTC)
    with ApiClient(GAMMA_URL, rate=rate) as gamma, ThreadPoolExecutor(workers) as pool:
        batches = list(
            tqdm(
                pool.map(lambda w: fetch_week(gamma, w), weeks(now)),
                total=len(weeks(now)),
                desc="polymarket weeks",
            )
        )
    seen: dict[str, dict] = {}
    for batch in batches:
        for m in batch:
            seen.setdefault(m["market_id"], m)
    markets = sorted(seen.values(), key=lambda m: m["close_time"])
    if len(markets) > config.POLYMARKET_MAX_MARKETS:
        rng = random.Random(config.RANDOM_SEED)
        markets = sorted(
            rng.sample(markets, config.POLYMARKET_MAX_MARKETS), key=lambda m: m["close_time"]
        )
    log.info("Polymarket: %d eligible markets; %d sampled", len(seen), len(markets))
    write_json(RAW / "markets.json", markets)
    with ApiClient(CLOB_URL, rate=rate) as clob, ThreadPoolExecutor(workers) as pool:
        list(
            tqdm(
                pool.map(lambda m: fetch_history(clob, m), markets),
                total=len(markets),
                desc="polymarket history",
            )
        )


def load_markets() -> list[dict[str, Any]]:
    return read_json(RAW / "markets.json")
