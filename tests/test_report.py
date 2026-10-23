import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from pmcal import config, report


def synthetic_dataset(n_markets=3_000, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for platform in ("Kalshi", "Polymarket"):
        for i in range(n_markets):
            p_true = rng.uniform(0.02, 0.98)
            outcome = int(rng.uniform() < p_true)
            event = f"{platform}-E{i // 3}"
            for horizon, hours in (("30d", 720), ("7d", 168), ("1d", 24), ("1h", 1)):
                price = float(np.clip(p_true + rng.normal(0, 0.03), 0.01, 0.99))
                rows.append(
                    {
                        "platform": platform,
                        "market_id": f"{platform}-{i}",
                        "event_id": event,
                        "series_id": event,
                        "category": ["Sports", "Politics", "Economics"][i % 3],
                        "title": "",
                        "volume": float(rng.lognormal(9, 1)),
                        "open_time": datetime(2025, 1, 1, tzinfo=UTC),
                        "close_time": datetime(2024 + i % 3, 3 + (i % 2) * 6, 1, tzinfo=UTC),
                        "duration_days": 59.0,
                        "outcome": outcome,
                        "horizon": horizon,
                        "horizon_hours": float(hours),
                        "price": price,
                        "bid": price - 0.005 if platform == "Kalshi" else None,
                        "ask": price + 0.005 if platform == "Kalshi" else None,
                        "closed_early": None if platform == "Kalshi" else bool(i % 10 == 0),
                        "volume_24h": float(rng.poisson(50) * (i % 3 > 0))
                        if platform == "Kalshi"
                        else None,
                    }
                )
    return pd.DataFrame(rows)


def test_report_runs_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATASET_PATH", tmp_path / "obs.parquet")
    monkeypatch.setattr(config, "FIGURES_DIR", tmp_path / "figures")
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "SITE_DATA_PATH", tmp_path / "site" / "results.json")
    monkeypatch.setattr(config, "SOCIAL_CARD_PATH", tmp_path / "site" / "og-image.png")
    synthetic_dataset().to_parquet(config.DATASET_PATH)

    result = report.run()

    figures = {p.name for p in (tmp_path / "figures").iterdir()}
    assert figures == {
        "reliability_by_horizon.png",
        "calibration_gap.png",
        "platform_comparison.png",
        "returns_by_price.png",
        "slopes_by_group.png",
        "slopes_over_time.png",
    }
    text = (tmp_path / "reports" / "results.md").read_text()
    assert "## Calibration by platform and horizon" in text
    assert "## Sensitivity: Polymarket markets that closed on schedule" in text
    assert len(result["summary"]) == 8
    site = json.loads((tmp_path / "site" / "results.json").read_text())
    assert site["totals"]["markets"] == 6_000
    assert {r["platform"] for r in site["reliability"]} == {"Kalshi", "Polymarket"}
    assert site["headline"]["kalshi"]["horizon"] == "1d"
    assert (tmp_path / "site" / "og-image.png").exists()
    assert site["by_period"] and site["forecast"] and site["composition"]
    assert any(r["group"] == "Recent trading" for r in site["slopes"])
    assert "NaN" not in (tmp_path / "site" / "results.json").read_text()
    # Synthetic prices are calibrated (with noise), so slopes should be near 1.
    for row in result["summary"]:
        assert 0.8 < row["b"] < 1.2
