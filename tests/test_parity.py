"""Train/serve feature parity test verifying zero skew between batch training and live inference."""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipelines.data.features import FEATURE_COLUMNS, build_features


def test_train_vs_serve_feature_parity(sample_market_frames) -> None:
    eq, bench, cues, news = sample_market_frames
    all_dates = sorted(pd.to_datetime(eq["trade_date"]).dt.date.unique())
    cutoff_day = all_dates[55]

    full_features = build_features(eq, bench, cues, news, include_labels=True)

    eq_hist = eq[pd.to_datetime(eq["trade_date"]).dt.date <= cutoff_day].copy()
    bench_hist = bench[pd.to_datetime(bench["trade_date"]).dt.date <= cutoff_day].copy()
    cues_hist = cues[pd.to_datetime(cues["trade_date"]).dt.date <= cutoff_day].copy()
    news_hist = news[pd.to_datetime(news["known_as_of"]).dt.tz_convert("Asia/Kolkata").dt.date <= cutoff_day].copy()

    serve_features = build_features(eq_hist, bench_hist, cues_hist, news_hist, include_labels=False)

    full_day = full_features[pd.to_datetime(full_features["trade_date"]).dt.date == cutoff_day].sort_values("symbol")
    serve_day = serve_features[pd.to_datetime(serve_features["trade_date"]).dt.date == cutoff_day].sort_values("symbol")

    assert list(full_day["symbol"]) == list(serve_day["symbol"])
    np.testing.assert_allclose(
        full_day[FEATURE_COLUMNS].to_numpy(dtype=float),
        serve_day[FEATURE_COLUMNS].to_numpy(dtype=float),
        rtol=1e-9,
        atol=1e-12,
    )
