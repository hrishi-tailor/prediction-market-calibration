"""Project-wide settings: paths, horizons and sampling limits."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DATASET_PATH = DATA_DIR / "observations.parquet"
FIGURES_DIR = ROOT / "figures"
REPORTS_DIR = ROOT / "reports"

# Horizons before close at which a market's price is observed. The price at close
# is useless for calibration: by then the outcome is usually known.
HORIZONS: dict[str, timedelta] = {
    "30d": timedelta(days=30),
    "7d": timedelta(days=7),
    "1d": timedelta(days=1),
    "1h": timedelta(hours=1),
}

# How stale the last observed price may be at each horizon before we drop it.
STALENESS: dict[str, timedelta] = {
    "30d": timedelta(days=3),
    "7d": timedelta(days=1),
    "1d": timedelta(hours=6),
    "1h": timedelta(hours=2),
}

# Minimum lifetime volume for a market to be included (contracts on Kalshi,
# USD on Polymarket). Below this, prices are mostly stale quotes.
KALSHI_MIN_VOLUME = 1_000
POLYMARKET_MIN_VOLUME = 10_000

# High-frequency series (hourly temperature, 15-minute crypto) list thousands of
# near-identical markets. Capping each series stops a few of them dominating.
KALSHI_MAX_LISTED_PER_SERIES = 40
KALSHI_MAX_MARKETS_PER_SERIES = 10
# Overall caps, sampled uniformly, to bound how long price-history fetching takes.
KALSHI_MAX_MARKETS = 25_000
# Series with less lifetime volume can hold only a handful of eligible markets.
KALSHI_MIN_SERIES_VOLUME = 10_000
POLYMARKET_MAX_MARKETS_PER_EVENT = 10

RANDOM_SEED = 20261021
