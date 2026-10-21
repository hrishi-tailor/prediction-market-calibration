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


def drivers_model(df: pd.DataFrame) -> pd.DataFrame:
    """One joint model: does the slope vary with category, horizon and volume?

    Each `lp:` term is the change in calibration slope relative to the baseline
    level (horizon 30d, the largest category), holding the others fixed.
    """
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
    """Average return per dollar from buying Yes in each price bucket.

    Kalshi buys pay the ask plus the taker fee. Polymarket has no order-book
    history here, so it buys at the sampled price with no fee, which flatters it.
    """
    is_kalshi = (df["platform"] == "Kalshi").to_numpy()
    price = df["price"].to_numpy(float)
    ask = df["ask"].to_numpy(float)
    cost = np.where(is_kalshi, ask + kalshi_taker_fee(ask), price)
    cost_no_fee = np.where(is_kalshi, ask, price)
    y = df["outcome"].to_numpy(float)
    ok = cost > 0
    frame = df.loc[ok].assign(
        ret=(y[ok] - cost[ok]) / cost[ok], ret_no_fee=(y[ok] - cost_no_fee[ok]) / cost_no_fee[ok]
    )
    bins = assign_bins(frame["price"], edges)
    n_bins = len(edges) - 1
    codes = pd.factorize(frame["event_id"])[0]
    n_clusters = codes.max() + 1
    flat = codes * n_bins + bins

    def sums(values):
        return np.bincount(flat, weights=values, minlength=n_clusters * n_bins).reshape(
            n_clusters, n_bins
        )

    n = sums(np.ones(len(frame)))
    r = sums(frame["ret"].to_numpy())
    r0 = sums(frame["ret_no_fee"].to_numpy())
    w = bootstrap_weights(n_clusters, n_boot, seed=config.RANDOM_SEED + 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = (w @ r) / (w @ n)
        lo, hi = np.nanpercentile(boot, [2.5, 97.5], axis=0)
        mean = r.sum(0) / n.sum(0)
        mean0 = r0.sum(0) / n.sum(0)
    table = pd.DataFrame(
        {
            "bin_low": edges[:-1],
            "bin_high": edges[1:],
            "n": n.sum(0).astype(int),
            "mean_return": mean,
            "mean_return_no_fee": mean0,
            "ci_low": lo,
            "ci_high": hi,
        }
    )
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
