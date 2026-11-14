"""Automated zero-leakage and walk-forward chronological split tests."""

from __future__ import annotations

import pandas as pd
import pytest

from pipelines.data.features import LeakageError, assert_no_leakage, build_features
from pipelines.training.walk_forward import make_walk_forward_splits


def test_build_features_passes_leakage_gate(sample_market_frames) -> None:
    eq, bench, cues, news = sample_market_frames
    feat = build_features(eq, bench, cues, news, include_labels=True)
    assert not feat.empty
    assert_no_leakage(feat)
    assert (
        pd.to_datetime(feat["feature_known_as_of"], utc=True) <= pd.to_datetime(feat["decision_ts"], utc=True)
    ).all()


def test_injected_future_feature_triggers_leakage_error(sample_market_frames) -> None:
    eq, bench, cues, news = sample_market_frames
    feat = build_features(eq, bench, cues, news, include_labels=False)
    corrupted = feat.copy()
    corrupted.loc[corrupted.index[0], "feature_known_as_of"] = corrupted.loc[
        corrupted.index[0], "decision_ts"
    ] + pd.Timedelta(minutes=1)
    with pytest.raises(LeakageError):
        assert_no_leakage(corrupted)


def test_future_cue_not_used_before_known_as_of(sample_market_frames) -> None:
    eq, bench, cues, news = sample_market_frames
    # Inject extreme future US cue timestamped at 16:00 IST on the last trade date
    last_day = pd.to_datetime(eq["trade_date"]).dt.date.max()
    cues_mod = cues.copy()
    mask = (cues_mod["symbol"] == "^GSPC") & (pd.to_datetime(cues_mod["trade_date"]).dt.date == last_day)
    cues_mod.loc[mask, "ret_1d"] = 999.0
    cues_mod.loc[mask, "known_as_of"] = pd.Timestamp(f"{last_day} 16:00:00+05:30")

    feat = build_features(eq, bench, cues_mod, news, include_labels=False)
    last_rows = feat[pd.to_datetime(feat["trade_date"]).dt.date == last_day]
    assert (last_rows["us_overnight_return"].abs() < 10.0).all()


def test_walk_forward_splits_strict_chronology_and_purge(sample_market_frames) -> None:
    eq, bench, cues, news = sample_market_frames
    feat = build_features(eq, bench, cues, news, include_labels=True)
    folds, test_idx = make_walk_forward_splits(feat, n_folds=3, purge_days=2)

    assert len(folds) >= 1
    assert len(test_idx) > 0
    test_min = pd.to_datetime(feat.loc[test_idx, "trade_date"]).dt.date.min()

    for fold in folds:
        tr_max = pd.to_datetime(feat.loc[fold.train_idx, "trade_date"]).dt.date.max()
        va_min = pd.to_datetime(feat.loc[fold.val_idx, "trade_date"]).dt.date.min()
        va_max = pd.to_datetime(feat.loc[fold.val_idx, "trade_date"]).dt.date.max()
        assert tr_max < va_min
        assert va_max < test_min
        assert set(fold.train_idx).isdisjoint(set(fold.val_idx))
        assert set(fold.train_idx).isdisjoint(set(test_idx))
        assert set(fold.val_idx).isdisjoint(set(test_idx))
