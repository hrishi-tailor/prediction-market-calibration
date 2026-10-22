"""Runs the full analysis and writes figures/ and reports/results.md."""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd

from pmcal import config, dataset, metrics, models, plots, site_data

log = logging.getLogger(__name__)

# Headline horizon for single-horizon views: far enough out that the outcome is
# rarely known yet, close enough that most markets are already open.
HEADLINE_HORIZON = "1d"
MAX_CATEGORIES = 8


def md_table(df: pd.DataFrame, formats: dict[str, str] | None = None) -> str:
    formats = formats or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if c in formats and isinstance(v, int | float | np.floating | np.integer):
                cells.append(format(v, formats[c]))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def summary_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (platform, horizon), g in df.groupby(["platform", "horizon"], observed=True):
        parts = metrics.murphy_decomposition(g["price"], g["outcome"])
        fit = models.calibration_fit(g)
        # Kalshi quotes in whole cents, so near-certain outcomes cannot be priced
        # above ~99c; that floor alone pushes extreme prices toward 50c and mimics
        # the bias. Refitting on 5-95c prices shows how much survives without it.
        mid = models.calibration_fit(g[g["price"].between(0.05, 0.95)])
        rows.append(
            {
                "platform": platform,
                "horizon": horizon,
                "n": len(g),
                "events": g["event_id"].nunique(),
                "yes_rate": g["outcome"].mean(),
                "mean_price": g["price"].mean(),
                "brier": parts["brier"],
                "reliability": parts["reliability"],
                "resolution": parts["resolution"],
                "ece": metrics.expected_calibration_error(g["price"], g["outcome"]),
                "a": fit["a"],
                "b": fit["b"],
                "b_low": fit["b_low"],
                "b_high": fit["b_high"],
                "p_b1": fit["p_b1"],
                "b_mid": mid["b"],
                "b_mid_low": mid["b_low"],
                "b_mid_high": mid["b_high"],
            }
        )
    return pd.DataFrame(rows)


def top_categories(df: pd.DataFrame) -> pd.Series:
    """Keeps each platform's largest categories by event count; folds the rest into Other."""
    keep = (
        df.groupby(["platform", "category"])["event_id"]
        .nunique()
        .groupby(level=0, group_keys=False)
        .nlargest(MAX_CATEGORIES)
        .index
    )
    keys = pd.MultiIndex.from_frame(df[["platform", "category"]])
    return pd.Series(np.where(keys.isin(keep), df["category"], "Other"), index=df.index)


def subgroup_slopes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.assign(category=top_categories(df), volume_tercile=models.volume_terciles(df))
    head = df[df["horizon"] == HEADLINE_HORIZON]
    frames = []
    for platform in df["platform"].unique():
        p_all, p_head = df[df["platform"] == platform], head[head["platform"] == platform]
        for column, data, prefix in (
            ("horizon", p_all, "Horizon"),
            ("volume_tercile", p_head, "Volume"),
            ("category", p_head, "Category"),
        ):
            s = models.slopes_by(data, column)
            if s.empty:
                continue
            s["platform"] = platform
            s["group"] = prefix
            s["label"] = f"{platform} · {prefix}: " + s[column].astype(str)
            s["level"] = s[column].astype(str)
            frames.append(s.drop(columns=[column]))
    return pd.concat(frames, ignore_index=True)


def run() -> dict[str, Any]:
    df = dataset.load()
    df["horizon"] = pd.Categorical(df["horizon"], categories=list(config.HORIZONS), ordered=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    platforms = [p for p in ("Kalshi", "Polymarket") if p in set(df["platform"])]

    summary = summary_rows(df)
    reliability = {
        (p, h): metrics.reliability_table(g)
        for (p, h), g in df.groupby(["platform", "horizon"], observed=True)
    }

    k = df[df["platform"] == "Kalshi"]
    plots.reliability_grid(
        {h: reliability[("Kalshi", h)] for h in config.HORIZONS if ("Kalshi", h) in reliability},
        {
            r["horizon"]: {"n": r["n"], "brier": r["brier"]}
            for _, r in summary[summary["platform"] == "Kalshi"].iterrows()
        },
        "Kalshi: realized frequency vs price, by time before close",
    )
    plots.gap_plot(
        {
            p: {h: reliability[(p, h)] for h in config.HORIZONS if (p, h) in reliability}
            for p in platforms
        }
    )
    plots.platform_comparison(
        {p: reliability[(p, HEADLINE_HORIZON)] for p in platforms}, HEADLINE_HORIZON
    )
    plots.social_card(
        {p: reliability[(p, HEADLINE_HORIZON)] for p in platforms},
        {"markets": int(df["market_id"].nunique())},
        config.SOCIAL_CARD_PATH,
    )
    k_head = k[k["horizon"] == HEADLINE_HORIZON]
    returns = models.returns_by_bucket(k_head)
    plots.returns_plot(returns, "Kalshi", HEADLINE_HORIZON)
    slopes = subgroup_slopes(df)
    plots.slope_forest(slopes)

    drivers = {p: models.drivers_model(df[df["platform"] == p]) for p in platforms}
    carry = {d: models.carry_distortion(d) for d in (1, 7, 30)}

    on_time = df[(df["platform"] == "Polymarket") & (df["closed_early"] == False)]  # noqa: E712
    on_time_summary = summary_rows(on_time) if len(on_time) else pd.DataFrame()

    write_markdown(df, summary, reliability, returns, slopes, drivers, carry, on_time_summary)
    result = {
        "summary": summary.to_dict(orient="records"),
        "polymarket_on_time": on_time_summary.to_dict(orient="records"),
        "slopes": slopes.drop(columns=["label"]).to_dict(orient="records"),
        "carry_distortion": carry,
    }
    with open(config.REPORTS_DIR / "summary.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    site_data.write(
        site_data.build(
            df, summary, reliability, returns, slopes, carry, on_time_summary, HEADLINE_HORIZON
        )
    )
    log.info("Wrote figures to %s and report to %s", config.FIGURES_DIR, config.REPORTS_DIR)
    return result


def write_markdown(
    df, summary, reliability, returns, slopes, drivers, carry, on_time_summary
) -> None:
    pct = ".1%"
    lines = ["# Results", "", "Generated by `pmcal analyze`. Do not edit by hand.", ""]
    lines += ["## Sample", ""]
    sample = (
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
    sample["first_close"] = pd.to_datetime(sample["first_close"]).dt.date
    sample["last_close"] = pd.to_datetime(sample["last_close"]).dt.date
    lines += [md_table(sample), ""]

    lines += ["## Calibration by platform and horizon", ""]
    lines += [
        "`b` is the calibration slope from `P(yes) = sigmoid(a + b·logit(price))`, with "
        "95% CIs clustered by event; `p(b=1)` tests perfect calibration. `reliability` is "
        "the calibration part of the Brier score (lower is better), `resolution` the "
        "informative part (higher is better). `b_5_95` refits the slope on prices "
        "between 5¢ and 95¢ only, removing the tick-size floor at the extremes.",
        "",
    ]
    lines += [
        md_table(
            summary.assign(
                b_ci=summary.apply(lambda r: f"[{r.b_low:.2f}, {r.b_high:.2f}]", axis=1),
                b_5_95=summary.apply(
                    lambda r: f"{r.b_mid:.3f} [{r.b_mid_low:.2f}, {r.b_mid_high:.2f}]", axis=1
                ),
            )[
                [
                    "platform",
                    "horizon",
                    "n",
                    "events",
                    "yes_rate",
                    "brier",
                    "reliability",
                    "resolution",
                    "ece",
                    "a",
                    "b",
                    "b_ci",
                    "p_b1",
                    "b_5_95",
                ]
            ].rename(columns={"p_b1": "p(b=1)"}),
            {
                "yes_rate": pct,
                "brier": ".4f",
                "reliability": ".5f",
                "resolution": ".4f",
                "ece": ".4f",
                "a": ".3f",
                "b": ".3f",
                "p(b=1)": ".2g",
            },
        ),
        "",
    ]

    if not on_time_summary.empty:
        early = df[df["platform"] == "Polymarket"].groupby("market_id")["closed_early"].first()
        lines += ["## Sensitivity: Polymarket markets that closed on schedule", ""]
        lines += [
            f"{early.mean():.1%} of Polymarket markets closed more than a day before their "
            "scheduled end, usually because the event happened. Their close time depends "
            "on their outcome, so 'the price 1 day before close' is sampled at an "
            "outcome-dependent time, which biases it toward underpricing Yes. This table "
            "repeats the calibration on the markets that closed on schedule. That subset "
            "is itself selected on outcome (it drops markets whose event happened early, "
            "which tilts it toward No), so the two tables bracket the effect rather than "
            "remove it.",
            "",
            md_table(
                on_time_summary.assign(
                    b_ci=on_time_summary.apply(
                        lambda r: f"[{r.b_low:.2f}, {r.b_high:.2f}]", axis=1
                    ),
                    gap=on_time_summary["yes_rate"] - on_time_summary["mean_price"],
                )[["horizon", "n", "events", "yes_rate", "gap", "brier", "ece", "a", "b", "b_ci"]],
                {
                    "yes_rate": pct,
                    "gap": "+.1%",
                    "brier": ".4f",
                    "ece": ".4f",
                    "a": ".3f",
                    "b": ".3f",
                },
            ),
            "",
        ]

    lines += ["## Reliability tables", ""]
    for (platform, horizon), table in reliability.items():
        lines += [f"### {platform}, {horizon} before close", ""]
        t = table.assign(
            bucket=table.apply(lambda r: f"{r.bin_low * 100:.0f}–{r.bin_high * 100:.0f}¢", axis=1),
            ci=table.apply(lambda r: f"[{r.ci_low:.1%}, {r.ci_high:.1%}]", axis=1),
        )[["bucket", "n", "mean_price", "freq", "ci", "gap"]]
        lines += [
            md_table(t, {"mean_price": pct, "freq": pct, "gap": "+.1%"}),
            "",
        ]

    lines += [
        f"## Returns from trading at the quote on Kalshi, {HEADLINE_HORIZON} before close",
        "",
    ]
    lines += [
        "Buying Yes at the ask, or No at 1 − bid, each paying Kalshi's taker fee "
        "`0.07·P·(1−P)` per contract. Buckets are by Yes midpoint. CIs are "
        "event-clustered bootstrap.",
        "",
    ]
    r = returns.assign(
        bucket=returns.apply(lambda r: f"{r.bin_low * 100:.0f}–{r.bin_high * 100:.0f}¢", axis=1),
        yes_ci=returns.apply(lambda r: f"[{r.yes_ci_low:+.1%}, {r.yes_ci_high:+.1%}]", axis=1),
        no_ci=returns.apply(lambda r: f"[{r.no_ci_low:+.1%}, {r.no_ci_high:+.1%}]", axis=1),
    )[["bucket", "n", "yes_no_fee_return", "yes_return", "yes_ci", "no_return", "no_ci"]]
    lines += [
        md_table(r, {c: "+.1%" for c in ("yes_no_fee_return", "yes_return", "no_return")}),
        "",
    ]

    lines += ["## Calibration slope by subgroup", ""]
    s = slopes.assign(ci=slopes.apply(lambda r: f"[{r.b_low:.2f}, {r.b_high:.2f}]", axis=1))[
        ["platform", "group", "level", "n", "events", "b", "ci", "p_b1"]
    ].rename(columns={"p_b1": "p(b=1)"})
    lines += [md_table(s, {"b": ".3f", "p(b=1)": ".2g"}), ""]

    lines += ["## Joint model of what drives the slope", ""]
    lines += [
        "Interaction terms `lp:x` are the change in calibration slope for level x "
        "relative to the baseline (30d horizon, largest category), holding the others "
        "fixed. `lp:log_volume_z` is the change per standard deviation of log volume.",
        "",
    ]
    for platform, table in drivers.items():
        t = table.reset_index(names="term")
        lines += [
            f"### {platform}",
            "",
            md_table(t, {c: ".3f" for c in ("coef", "low", "high")} | {"p_value": ".2g"}),
            "",
        ]

    lines += ["## Cost of capital", ""]
    lines += [
        "Largest possible shift in a midpoint from discounting locked-up capital at 5% a "
        "year (see `models.carry_distortion`):",
        "",
    ]
    lines += [f"- {d} days: {v * 100:.2f}¢" for d, v in carry.items()]
    lines.append("")
    (config.REPORTS_DIR / "results.md").write_text("\n".join(lines))
