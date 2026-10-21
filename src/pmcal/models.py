"""Regression tests for miscalibration and the favourite-longshot bias.

The workhorse is a logistic calibration regression,

    P(yes) = sigmoid(a + b * logit(price)).

A perfectly calibrated market has a = 0 and b = 1. A slope b > 1 means true
probabilities are more extreme than prices: longshots are overpriced and
favourites underpriced, which is the favourite-longshot bias. A slope b < 1
means prices are overconfident. The slope does not depend on whether a market is
quoted from the Yes or the No side (flipping the side negates both logit(price)
and the outcome's log-odds), so it is a clean summary of the bias.

Standard errors are clustered by event.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats

from pmcal import config
from pmcal.metrics import BIN_EDGES, assign_bins, bootstrap_weights

# Kalshi quotes in whole cents, so the extreme midpoint is 0.5c; clip there so
# logit(price) stays finite on both platforms.
PRICE_CLIP = 0.005


def logit(p: np.ndarray | pd.Series) -> np.ndarray:
    p = np.clip(np.asarray(p, float), PRICE_CLIP, 1 - PRICE_CLIP)
    return np.log(p / (1 - p))


def calibration_fit(df: pd.DataFrame) -> dict[str, float]:
    """Fits the calibration regression; returns a, b, their CIs and H0 tests."""
    x = sm.add_constant(logit(df["price"]))
    groups = pd.factorize(df["event_id"])[0]
    fit = sm.GLM(df["outcome"].to_numpy(float), x, family=sm.families.Binomial()).fit(
        cov_type="cluster", cov_kwds={"groups": groups}
    )
    a, b = fit.params
    se_a, se_b = fit.bse
    z = stats.norm.ppf(0.975)
    z_b = (b - 1) / se_b
    return {
        "n": len(df),
        "events": int(groups.max() + 1),
        "a": float(a),
        "a_low": float(a - z * se_a),
        "a_high": float(a + z * se_a),
        "b": float(b),
        "b_low": float(b - z * se_b),
        "b_high": float(b + z * se_b),
        "p_a0": float(2 * stats.norm.sf(abs(a / se_a))),
        "p_b1": float(2 * stats.norm.sf(abs(z_b))),
    }


def slopes_by(df: pd.DataFrame, column: str, min_events: int = 30) -> pd.DataFrame:
    """Calibration slope within each level of `column` (levels with too few events dropped)."""
    rows = []
    for level, group in df.groupby(column, observed=True):
        if group["event_id"].nunique() < min_events:
            continue
        rows.append({column: level, **calibration_fit(group)})
    return pd.DataFrame(rows)


def volume_terciles(df: pd.DataFrame) -> pd.Series:
    """Low / Mid / High volume, computed within each platform (units differ)."""
    return df.groupby("platform")["volume"].transform(
        lambda v: pd.qcut(v.rank(method="first"), 3, labels=["Low", "Mid", "High"])
    )


def fold_small_categories(categories: pd.Series, events: pd.Series, min_events: int) -> pd.Series:
    """Relabels categories with fewer than `min_events` distinct events as 'Other'.

    If 'Other' is itself still that small, it is merged into the largest category,
    so no level of the result is thin enough to separate perfectly.
    """
    counts = events.groupby(categories).nunique()
    small = counts[counts < min_events].index
    folded = categories.where(~categories.isin(small), "Other")
    if (folded == "Other").any() and events[folded == "Other"].nunique() < min_events:
        largest = events.groupby(folded).nunique().drop("Other").idxmax()
        folded = folded.where(folded != "Other", largest)
    return folded


def drivers_model(df: pd.DataFrame, min_events: int = 50) -> pd.DataFrame:
    """One joint model: does the slope vary with category, horizon and volume?

    Each `lp:` term is the change in calibration slope relative to the baseline
    level (horizon 30d, the largest category), holding the others fixed.
    Categories with few events are folded into 'Other': a handful of markets can
    separate perfectly, which makes the clustered covariance singular.
    """
    df = df.assign(category=fold_small_categories(df["category"], df["event_id"], min_events))
    data = df.assign(
        lp=logit(df["price"]),
        log_volume=np.log10(df["volume"]),
        horizon=df["horizon"].astype(str),
    )
    data["log_volume_z"] = (data["log_volume"] - data["log_volume"].mean()) / data[
        "log_volume"
    ].std()
    top = data["category"].value_counts().index[0]
    formula = (
        f"outcome ~ lp * (C(category, Treatment(reference={top!r}))"
        " + C(horizon, Treatment(reference='30d')) + log_volume_z)"
    )
    fit = smf.glm(formula, data, family=sm.families.Binomial()).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(data["event_id"])[0]}
    )
    out = pd.DataFrame(
        {
            "coef": fit.params,
            "low": fit.conf_int()[0],
            "high": fit.conf_int()[1],
            "p_value": fit.pvalues,
        }
    )
    out.index = out.index.str.replace(r", Treatment\(reference=[^)]*\)", "", regex=True)
    return out[out.index.str.startswith("lp")]


def kalshi_taker_fee(price: np.ndarray) -> np.ndarray:
    """Kalshi's taker fee per contract, 0.07 * P * (1 - P).

    Kalshi rounds the fee up to the next cent per order, so a single-contract
    order pays more; this is the large-order rate.
    """
    price = np.asarray(price, float)
    return 0.07 * price * (1 - price)


def returns_by_bucket(
    df: pd.DataFrame, edges: np.ndarray = BIN_EDGES, n_boot: int = 1000
) -> pd.DataFrame:
    """Average return per dollar from buying Yes, or buying No, in each price bucket.

    Needs bid/ask quotes (Kalshi). Buying Yes pays the ask; buying No pays
    1 - bid (the No ask). Both pay the taker fee. Buckets are by the Yes
    midpoint, so a low bucket's No column is the return from fading longshots.
    """
    bid, ask = df["bid"].to_numpy(float), df["ask"].to_numpy(float)
    y = df["outcome"].to_numpy(float)
    no_ask = 1 - bid
    yes_cost, no_cost = ask + kalshi_taker_fee(ask), no_ask + kalshi_taker_fee(no_ask)
    # A zero ask means nothing could be bought on that side.
    yes_ok, no_ok = (ask > 0).astype(float), (no_ask > 0).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        returns = {
            "yes": np.where(ask > 0, (y - yes_cost) / yes_cost, 0.0),
            "yes_no_fee": np.where(ask > 0, (y - ask) / ask, 0.0),
            "no": np.where(no_ask > 0, ((1 - y) - no_cost) / no_cost, 0.0),
        }
    bins = assign_bins(df["price"], edges)
    n_bins = len(edges) - 1
    codes = pd.factorize(df["event_id"])[0]
    n_clusters = codes.max() + 1
    flat = codes * n_bins + bins

    def sums(values: np.ndarray) -> np.ndarray:
        return np.bincount(flat, weights=values, minlength=n_clusters * n_bins).reshape(
            n_clusters, n_bins
        )

    w = bootstrap_weights(n_clusters, n_boot, seed=config.RANDOM_SEED + 1)
    columns: dict[str, np.ndarray] = {"n": np.bincount(bins, minlength=n_bins)}
    for name, counts in (("yes", yes_ok), ("yes_no_fee", yes_ok), ("no", no_ok)):
        n, r = sums(counts), sums(returns[name])
        with np.errstate(invalid="ignore", divide="ignore"):
            columns[f"{name}_return"] = r.sum(0) / n.sum(0)
            if name != "yes_no_fee":
                boot = (w @ r) / (w @ n)
                columns[f"{name}_ci_low"], columns[f"{name}_ci_high"] = np.nanpercentile(
                    boot, [2.5, 97.5], axis=0
                )
    table = pd.DataFrame({"bin_low": edges[:-1], "bin_high": edges[1:], **columns})
    return table[table["n"] > 0].reset_index(drop=True)


def carry_distortion(days: float, rate: float = 0.05) -> float:
    """Largest shift in a quoted midpoint caused by the cost of locked-up capital.

    If both sides of a contract are priced at their discounted fair value, Yes
    ask = q*d and No ask = (1-q)*d with d = exp(-r*T), so the midpoint is
    0.5 + d*(q - 0.5): prices are shrunk toward 50c. The shift is largest at
    q = 0 or 1, where it equals (1 - d) / 2.
    """
    d = np.exp(-rate * days / 365)
    return float((1 - d) / 2)
