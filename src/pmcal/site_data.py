"""Exports the numbers behind the project website to `site/data/results.json`.

The page reads only this file, so every figure and number it shows comes from
the same run as `reports/results.md` and cannot drift out of sync with it.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from pmcal import config

DECIMALS = 5


def _clean(value: Any) -> Any:
    """JSON-safe, rounded scalars: NaN becomes null, floats keep `DECIMALS` places."""
    if isinstance(value, float | int) and not isinstance(value, bool):
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, DECIMALS) if not value.is_integer() else int(value)
    if hasattr(value, "item"):  # numpy scalars
        return _clean(value.item())
    if isinstance(value, pd.Timestamp | datetime):
        return value.date().isoformat()
    return value


def _records(df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    return [{c: _clean(row[c]) for c in columns} for _, row in df[columns].iterrows()]


def build(
    df: pd.DataFrame,
    summary: pd.DataFrame,
    reliability: dict[tuple[str, str], pd.DataFrame],
    returns: pd.DataFrame,
    slopes: pd.DataFrame,
    carry: dict[int, float],
    on_time_summary: pd.DataFrame,
    headline_horizon: str,
    extras: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    extras = extras or {}
    platforms = (
        df.groupby("platform")
        .agg(
            markets=("market_id", "nunique"),
            events=("event_id", "nunique"),
            observations=("price", "size"),
            first_close=("close_time", "min"),
            last_close=("close_time", "max"),
        )
        .reset_index()
    )
    poly = df[df["platform"] == "Polymarket"]
    early_share = (
        poly.groupby("market_id")["closed_early"].first().dropna().astype(bool).mean()
        if "closed_early" in poly
        else None
    )

    def summary_row(platform: str, horizon: str) -> dict[str, Any]:
        row = summary[(summary["platform"] == platform) & (summary["horizon"] == horizon)]
        return {c: _clean(v) for c, v in row.iloc[0].items()} if len(row) else {}

    cheapest = returns.iloc[0] if len(returns) else None
    reliability_rows = []
    for (platform, horizon), table in reliability.items():
        for rec in _records(
            table, ["bin_low", "bin_high", "n", "mean_price", "freq", "ci_low", "ci_high", "gap"]
        ):
            reliability_rows.append({"platform": platform, "horizon": str(horizon), **rec})

    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "horizons": list(config.HORIZONS),
        "headline_horizon": headline_horizon,
        "totals": {
            "markets": int(df["market_id"].nunique()),
            "events": int(df["event_id"].nunique()),
            "observations": int(len(df)),
            "first_close": _clean(df["close_time"].min()),
            "last_close": _clean(df["close_time"].max()),
        },
        "platforms": _records(
            platforms,
            ["platform", "markets", "events", "observations", "first_close", "last_close"],
        ),
        "headline": {
            "kalshi": summary_row("Kalshi", headline_horizon),
            "polymarket": summary_row("Polymarket", headline_horizon),
            "kalshi_1h": summary_row("Kalshi", "1h"),
            "cheapest_bucket": None
            if cheapest is None
            else {
                "bin_low": _clean(cheapest["bin_low"]),
                "bin_high": _clean(cheapest["bin_high"]),
                "yes_return": _clean(cheapest["yes_return"]),
                "n": _clean(cheapest["n"]),
            },
        },
        "summary": _records(
            summary,
            [
                "platform",
                "horizon",
                "n",
                "events",
                "yes_rate",
                "mean_price",
                "brier",
                "ece",
                "a",
                "b",
                "b_low",
                "b_high",
                "p_b1",
                "b_mid",
                "b_mid_low",
                "b_mid_high",
            ],
        ),
        "reliability": reliability_rows,
        "returns": _records(
            returns,
            [
                "bin_low",
                "bin_high",
                "n",
                "yes_return",
                "yes_ci_low",
                "yes_ci_high",
                "no_return",
                "no_ci_low",
                "no_ci_high",
            ],
        ),
        "slopes": _records(
            slopes, ["platform", "group", "level", "n", "events", "b", "b_low", "b_high", "p_b1"]
        ),
        "polymarket_on_time": _records(
            on_time_summary, ["horizon", "n", "yes_rate", "mean_price", "b", "b_low", "b_high"]
        )
        if len(on_time_summary)
        else [],
        "by_period": _records(
            extras["by_period"],
            [
                "platform",
                "period",
                "n",
                "events",
                "b",
                "b_low",
                "b_high",
                "median_volume",
                "weather_share",
            ],
        )
        if "by_period" in extras
        else [],
        "composition": _records(
            extras["composition"], ["sample", "period", "n", "events", "b", "b_low", "b_high"]
        )
        if "composition" in extras and len(extras["composition"])
        else [],
        "forecast": _records(
            extras["forecast"],
            [
                "platform",
                "horizon",
                "split",
                "train_n",
                "test_n",
                "test_events",
                "b",
                "brier_raw",
                "brier_recal",
                "improvement",
                "improvement_low",
                "improvement_high",
            ],
        )
        if "forecast" in extras
        else [],
        "early_close_share": _clean(early_share),
        "carry_distortion": {str(k): _clean(v) for k, v in carry.items()},
    }


def write(data: dict[str, Any]) -> None:
    config.SITE_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.SITE_DATA_PATH, "w") as f:
        json.dump(data, f, indent=1, sort_keys=False)
        f.write("\n")
