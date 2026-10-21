import numpy as np
import pandas as pd
import pytest

from pmcal import metrics, models


def simulate(n=20_000, slope=1.0, seed=0, events=None):
    """Prices uniform on (0.01, 0.99); outcomes drawn from sigmoid(slope * logit(price))."""
    rng = np.random.default_rng(seed)
    price = rng.uniform(0.01, 0.99, n)
    true_p = 1 / (1 + np.exp(-slope * models.logit(price)))
    outcome = (rng.uniform(size=n) < true_p).astype(int)
    event_id = np.arange(n) if events is None else rng.integers(0, events, n)
    return pd.DataFrame(
        {
            "price": price,
            "outcome": outcome,
            "event_id": event_id.astype(str),
            "platform": "Kalshi",
            "ask": np.clip(price + 0.005, 0, 1),
            "volume": rng.lognormal(8, 1, n),
        }
    )


def test_wilson_interval_matches_known_value():
    lo, hi = metrics.wilson_interval(np.array([5]), np.array([10]))
    assert lo[0] == pytest.approx(0.2366, abs=1e-3)
    assert hi[0] == pytest.approx(0.7634, abs=1e-3)


def test_assign_bins_edges():
    bins = metrics.assign_bins(pd.Series([0.0, 0.019, 0.02, 0.5, 0.999, 1.0]))
    assert list(bins) == [0, 0, 1, 7, 13, 13]


def test_reliability_of_calibrated_market_covers_diagonal():
    table = metrics.reliability_table(simulate(), n_boot=300)
    inside = (table["ci_low"] <= table["mean_price"]) & (table["mean_price"] <= table["ci_high"])
    assert inside.mean() >= 0.85
    assert table["n"].sum() == 20_000


def test_clustered_intervals_are_wider_than_wilson_when_events_are_shared():
    df = simulate(events=200)
    # Make outcomes within an event perfectly correlated.
    df["outcome"] = df.groupby("event_id")["outcome"].transform("first")
    table = metrics.reliability_table(df, n_boot=300)
    clustered = (table["ci_high"] - table["ci_low"]).median()
    naive = (table["wilson_high"] - table["wilson_low"]).median()
    assert clustered > 2 * naive


def test_murphy_decomposition_adds_up():
    df = simulate()
    parts = metrics.murphy_decomposition(df["price"], df["outcome"])
    assert parts["reliability"] < 0.002
    assert abs(parts["residual"]) < 0.01
    assert parts["brier"] == pytest.approx(
        metrics.brier_score(df["price"], df["outcome"]), rel=1e-12
    )


def test_calibration_fit_recovers_slope():
    assert models.calibration_fit(simulate(slope=1.0))["p_b1"] > 0.01
    flb = models.calibration_fit(simulate(slope=1.4))
    assert flb["b_low"] > 1.2
    assert flb["p_b1"] < 1e-6


def test_slopes_by_drops_small_groups():
    df = simulate(n=4_000)
    df["group"] = np.where(np.arange(len(df)) < 10, "tiny", "big")
    out = models.slopes_by(df, "group")
    assert list(out["group"]) == ["big"]


def test_returns_at_fair_prices_lose_only_fees_and_spread():
    df = simulate(n=50_000)
    table = models.returns_by_bucket(df, n_boot=100)
    mid = table[(table["bin_low"] >= 0.3) & (table["bin_high"] <= 0.7)]
    assert (mid["mean_return_no_fee"].abs() < 0.05).all()
    assert (mid["mean_return"] < mid["mean_return_no_fee"]).all()


def test_carry_distortion_is_tiny_at_30_days():
    assert models.carry_distortion(30, rate=0.05) == pytest.approx(0.00205, abs=1e-4)
    assert models.carry_distortion(0) == 0
