"""Static figures for the README (matplotlib, PNG)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from pmcal import config  # noqa: E402

# Validated categorical order (blue, orange, aqua, yellow); neutral inks for text.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
PLATFORM_COLORS = {"Kalshi": SERIES[0], "Polymarket": SERIES[1]}
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e6e5e0"
SURFACE = "#ffffff"

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK_2,
        "axes.titlecolor": INK,
        "axes.titleweight": "bold",
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "font.family": "sans-serif",
        "savefig.dpi": 180,
        "savefig.bbox": "tight",
    }
)


def _cents(ax: plt.Axes, axis: str = "x") -> None:
    fmt = plt.FuncFormatter(lambda v, _: f"{v * 100:.0f}¢" if axis == "x" else f"{v * 100:.0f}%")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(fmt)


def _save(fig: plt.Figure, name: str) -> Path:
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.FIGURES_DIR / name
    fig.savefig(path)
    plt.close(fig)
    return path


def _errorbar(ax, table: pd.DataFrame, y: str, color: str, label: str | None = None) -> None:
    ax.errorbar(
        table["mean_price"]
        if "mean_price" in table
        else (table["bin_low"] + table["bin_high"]) / 2,
        table[y],
        yerr=[table[y] - table["ci_low"], table["ci_high"] - table[y]],
        fmt="o",
        ms=5,
        lw=1.5,
        capsize=0,
        color=color,
        mec=SURFACE,
        mew=1,
        label=label,
        zorder=3,
    )


def reliability_grid(tables: dict[str, pd.DataFrame], stats: dict[str, dict], title: str) -> Path:
    """Small multiples: one reliability diagram per horizon."""
    fig, axes = plt.subplots(1, len(tables), figsize=(3.2 * len(tables), 3.6), sharey=True)
    for ax, (horizon, table) in zip(np.atleast_1d(axes), tables.items(), strict=True):
        ax.plot([0, 1], [0, 1], color=MUTED, lw=1, ls="--", zorder=1)
        _errorbar(ax, table, "freq", SERIES[0])
        s = stats[horizon]
        ax.set_title(f"{horizon} before close")
        ax.text(
            0.04,
            0.96,
            f"n = {s['n']:,}\nBrier {s['brier']:.3f}",
            transform=ax.transAxes,
            va="top",
            fontsize=8.5,
            color=INK_2,
        )
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        _cents(ax, "x")
        _cents(ax, "y")
        ax.set_xlabel("Market price")
    np.atleast_1d(axes)[0].set_ylabel("Resolved Yes")
    fig.suptitle(title, x=0.01, y=1.0, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    return _save(fig, "reliability_by_horizon.png")


def gap_plot(gaps: dict[str, dict[str, pd.DataFrame]]) -> Path:
    """Realized frequency minus price by bucket, with 95% CIs: platforms x horizons.

    Reliability diagrams compress small but systematic errors near the diagonal;
    plotting the gap makes the favourite-longshot pattern visible.
    """
    platforms = list(gaps)
    horizons = list(config.HORIZONS)
    fig, axes = plt.subplots(
        len(platforms),
        len(horizons),
        figsize=(3.1 * len(horizons), 2.7 * len(platforms) + 0.6),
        sharex=True,
        sharey=True,
        squeeze=False,
    )
    for row, platform in enumerate(platforms):
        color = PLATFORM_COLORS[platform]
        for col, horizon in enumerate(horizons):
            ax = axes[row, col]
            ax.axhline(0, color=MUTED, lw=1)
            table = gaps[platform].get(horizon)
            if table is not None:
                ax.fill_between(
                    table["mean_price"],
                    table["ci_low"] - table["mean_price"],
                    table["ci_high"] - table["mean_price"],
                    color=color,
                    alpha=0.18,
                    lw=0,
                )
                ax.plot(
                    table["mean_price"],
                    table["gap"],
                    "-o",
                    color=color,
                    lw=2,
                    ms=4,
                    mec=SURFACE,
                    mew=0.8,
                )
            if row == 0:
                ax.set_title(f"{horizon} before close")
            if col == 0:
                ax.set_ylabel(f"{platform}\nResolved Yes − price")
            if row == len(platforms) - 1:
                ax.set_xlabel("Market price")
            ax.set_xlim(0, 1)
            ax.set_ylim(-0.15, 0.15)
            _cents(ax, "x")
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v * 100:+.0f} pp"))
    fig.suptitle(
        "Calibration gap with 95% CI (above zero: outcome more likely than priced)",
        x=0.01,
        y=1.0,
        ha="left",
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    fig.tight_layout()
    return _save(fig, "calibration_gap.png")


def platform_comparison(tables: dict[str, pd.DataFrame], horizon: str) -> Path:
    fig, ax = plt.subplots(figsize=(4.6, 4.4))
    ax.plot([0, 1], [0, 1], color=MUTED, lw=1, ls="--", zorder=1)
    for platform, table in tables.items():
        _errorbar(ax, table, "freq", PLATFORM_COLORS[platform], label=platform)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    _cents(ax, "x")
    _cents(ax, "y")
    ax.set_xlabel("Market price")
    ax.set_ylabel("Resolved Yes")
    ax.legend(loc="upper left")
    ax.set_title(f"Kalshi vs Polymarket, {horizon} before close", loc="left")
    return _save(fig, "platform_comparison.png")


def returns_plot(table: pd.DataFrame, platform: str, horizon: str) -> Path:
    """Return per $1 from buying Yes vs buying No in each bucket, after fees."""
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    labels = [
        f"{lo * 100:.0f}–{hi * 100:.0f}¢"
        for lo, hi in zip(table["bin_low"], table["bin_high"], strict=True)
    ]
    pos = np.arange(len(table))
    width = 0.38
    ax.axhline(0, color=MUTED, lw=1)
    for offset, side, color in ((-width / 2, "yes", SERIES[0]), (width / 2, "no", SERIES[1])):
        mean = table[f"{side}_return"]
        ax.bar(
            pos + offset,
            mean,
            width=width - 0.04,
            color=color,
            label=f"Buy {side.capitalize()}",
            zorder=2,
        )
        ax.errorbar(
            pos + offset,
            mean,
            yerr=[mean - table[f"{side}_ci_low"], table[f"{side}_ci_high"] - mean],
            fmt="none",
            ecolor=INK_2,
            lw=1,
            zorder=3,
        )
    ax.set_xticks(pos, labels, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v * 100:+.0f}%"))
    ax.set_ylabel("Average return per $1, after fees")
    ax.set_xlabel("Yes price (midpoint)")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="lower right")
    ax.set_title(f"{platform}: trading at the quote, {horizon} before close", loc="left")
    return _save(fig, "returns_by_price.png")


def slope_forest(rows: pd.DataFrame) -> Path:
    """Calibration slope with 95% CI per subgroup, one panel per platform; 1 is calibrated."""
    platforms = [p for p in PLATFORM_COLORS if p in set(rows["platform"])]
    height = 0.3 * rows.groupby("platform").size().max() + 1.4
    fig, axes = plt.subplots(1, len(platforms), figsize=(5.4 * len(platforms), height), sharex=True)
    for ax, platform in zip(np.atleast_1d(axes), platforms, strict=True):
        sub = rows[rows["platform"] == platform].iloc[::-1].reset_index(drop=True)
        color = PLATFORM_COLORS[platform]
        ax.axvline(1, color=MUTED, lw=1, ls="--")
        ax.errorbar(
            sub["b"],
            sub.index,
            xerr=[sub["b"] - sub["b_low"], sub["b_high"] - sub["b"]],
            fmt="o",
            ms=5,
            lw=1.5,
            color=color,
            mec=SURFACE,
            mew=0.8,
        )
        ax.set_yticks(sub.index, sub["group"] + ": " + sub["level"])
        # Separate the horizon, volume and category blocks.
        for i in sub.index[1:]:
            if sub.loc[i, "group"] != sub.loc[i - 1, "group"]:
                ax.axhline(i - 0.5, color=GRID, lw=1)
        ax.grid(axis="y", visible=False)
        ax.set_title(platform, loc="left")
        ax.set_xlabel("Calibration slope b")
    fig.suptitle(
        "b > 1: longshots overpriced, favourites underpriced.  b < 1: prices overconfident.",
        x=0.01,
        y=1.0,
        ha="left",
        fontsize=11,
        fontweight="bold",
        color=INK,
    )
    fig.tight_layout()
    return _save(fig, "slopes_by_group.png")
