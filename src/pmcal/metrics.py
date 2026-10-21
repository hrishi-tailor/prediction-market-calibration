"""Calibration metrics: reliability tables, Brier score and its decomposition.

Markets in the same event (for example the brackets of one CPI release) resolve
together, so they are not independent. Confidence intervals therefore come from
a cluster bootstrap that resamples whole events, not individual markets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from pmcal import config

# Ten equal-width buckets, with the tails split finer: most of the
# favourite-longshot action is below 10c and above 90c.
BIN_EDGES = np.array([0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 1])


def wilson_interval(k: np.ndarray, n: np.ndarray, level: float = 0.95) -> tuple:
    """Wilson score interval for a binomial proportion (well-behaved near 0 and 1)."""
    k, n = np.asarray(k, float), np.asarray(n, float)
    z = stats.norm.ppf(0.5 + level / 2)
    p = np.divide(k, n, out=np.full_like(k, np.nan), where=n > 0)
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return centre - half, centre + half


def assign_bins(price: pd.Series, edges: np.ndarray = BIN_EDGES) -> np.ndarray:
    """Bin index for each price; a price of exactly 1 goes in the last bin."""
    return np.clip(np.searchsorted(edges, price.to_numpy(), side="right") - 1, 0, len(edges) - 2)


def _cluster_sums(df: pd.DataFrame, values: dict[str, np.ndarray], n_bins: int, bins: np.ndarray):
    """Per-cluster, per-bin sums of each value array: {name: (n_clusters, n_bins)}."""
    codes, _ = pd.factorize(df["event_id"])
    n_clusters = codes.max() + 1
    flat = codes * n_bins + bins
    return {
        name: np.bincount(flat, weights=v, minlength=n_clusters * n_bins).reshape(
            n_clusters, n_bins
        )
        for name, v in values.items()
    }


def bootstrap_weights(n_clusters: int, n_boot: int, seed: int = config.RANDOM_SEED) -> np.ndarray:
    """Cluster-bootstrap weights: each row says how often each cluster is drawn."""
    rng = np.random.default_rng(seed)
    return rng.multinomial(n_clusters, np.full(n_clusters, 1 / n_clusters), size=n_boot)


def reliability_table(
    df: pd.DataFrame, edges: np.ndarray = BIN_EDGES, n_boot: int = 1000
) -> pd.DataFrame:
    """Per price bucket: count, mean price, realized frequency and 95% CIs.

    `ci_low`/`ci_high` are event-clustered bootstrap percentiles; `wilson_*` are
    the naive independent-markets intervals, kept for comparison.
    """
    n_bins = len(edges) - 1
    bins = assign_bins(df["price"], edges)
    ones = np.ones(len(df))
    sums = _cluster_sums(
        df,
        {"n": ones, "k": df["outcome"].to_numpy(float), "p": df["price"].to_numpy(float)},
        n_bins,
        bins,
    )
    n, k, p = (sums[x].sum(axis=0) for x in ("n", "k", "p"))
    w = bootstrap_weights(sums["n"].shape[0], n_boot)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = (w @ sums["k"]) / (w @ sums["n"])
        lo, hi = np.nanpercentile(boot, [2.5, 97.5], axis=0)
        freq = k / n
        mean_price = p / n
    wl, wh = wilson_interval(k, n)
    table = pd.DataFrame(
        {
            "bin_low": edges[:-1],
            "bin_high": edges[1:],
            "n": n.astype(int),
            "mean_price": mean_price,
            "freq": freq,
            "ci_low": lo,
            "ci_high": hi,
            "wilson_low": wl,
            "wilson_high": wh,
        }
    )
    table["gap"] = table["freq"] - table["mean_price"]
    return table[table["n"] > 0].reset_index(drop=True)


def brier_score(price: np.ndarray, outcome: np.ndarray) -> float:
    return float(np.mean((np.asarray(price) - np.asarray(outcome)) ** 2))


def murphy_decomposition(
    price: pd.Series, outcome: pd.Series, edges: np.ndarray = BIN_EDGES
) -> dict[str, float]:
    """Brier = reliability - resolution + uncertainty (+ a within-bin residual).

    Reliability is the calibration error (lower is better); resolution is how far
    forecasts move away from the base rate (higher is better); uncertainty depends
    only on the base rate.
    """
    p, y = price.to_numpy(float), outcome.to_numpy(float)
    bins = assign_bins(price, edges)
    n_bins = len(edges) - 1
    n = np.bincount(bins, minlength=n_bins)
    mask = n > 0
    pk = np.bincount(bins, weights=p, minlength=n_bins)[mask] / n[mask]
    ok = np.bincount(bins, weights=y, minlength=n_bins)[mask] / n[mask]
    base = y.mean()
    w = n[mask] / len(y)
    reliability = float(np.sum(w * (pk - ok) ** 2))
    resolution = float(np.sum(w * (ok - base) ** 2))
    uncertainty = float(base * (1 - base))
    brier = brier_score(p, y)
    return {
        "brier": brier,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
        "residual": brier - (reliability - resolution + uncertainty),
    }


def expected_calibration_error(
    price: pd.Series, outcome: pd.Series, edges: np.ndarray = BIN_EDGES
) -> float:
    """Count-weighted mean |realized frequency - mean price| across buckets."""
    bins = assign_bins(price, edges)
    frame = pd.DataFrame({"b": bins, "p": price.to_numpy(), "y": outcome.to_numpy()})
    g = frame.groupby("b").agg(n=("y", "size"), p=("p", "mean"), y=("y", "mean"))
    return float(np.sum(g["n"] * (g["y"] - g["p"]).abs()) / g["n"].sum())
