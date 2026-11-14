"""Strict chronological walk-forward splitter with purge gap and untouched final test holdout."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_idx: pd.Index
    val_idx: pd.Index
    train_start: str
    train_end: str
    val_start: str
    val_end: str


def make_walk_forward_splits(
    df: pd.DataFrame,
    date_col: str = "trade_date",
    n_folds: int = 4,
    min_train_days: int = 250,
    val_days: int = 60,
    test_holdout_days: int = 90,
    purge_days: int = 2,
) -> tuple[list[WalkForwardFold], pd.Index]:
    """Build expanding walk-forward folds and an untouched chronological holdout test index.

    Never shuffles dates; enforces strict `max(train_date) + purge_days < min(val_date)`
    and `max(val_date) + purge_days < min(test_date)`.
    """
    unique_dates = sorted(pd.to_datetime(df[date_col]).dt.date.unique())
    total = len(unique_dates)
    if total < min_train_days + val_days + test_holdout_days + purge_days * 2:
        # Scale proportionally if history has fewer trading days (e.g., compact unit tests)
        test_holdout_days = max(5, total // 6)
        val_days = max(5, total // 8)
        min_train_days = max(10, total - test_holdout_days - val_days * min(n_folds, 2) - purge_days * 2)

    dev_end = total - test_holdout_days
    test_dates = set(unique_dates[dev_end:])
    dev_dates = unique_dates[: dev_end - purge_days]

    folds: list[WalkForwardFold] = []
    usable = len(dev_dates) - min_train_days - purge_days
    if usable < val_days:
        val_days = max(5, usable)
    actual_folds = max(1, min(n_folds, usable // val_days))

    for k in range(actual_folds):
        val_end_i = len(dev_dates) - (actual_folds - 1 - k) * val_days
        val_start_i = val_end_i - val_days
        train_end_i = val_start_i - purge_days
        if train_end_i < min_train_days:
            continue
        tr_dates = set(dev_dates[:train_end_i])
        va_dates = set(dev_dates[val_start_i:val_end_i])
        dt_series = pd.to_datetime(df[date_col]).dt.date
        tr_idx = df.index[dt_series.isin(tr_dates)]
        va_idx = df.index[dt_series.isin(va_dates)]
        folds.append(
            WalkForwardFold(
                fold_id=k + 1,
                train_idx=tr_idx,
                val_idx=va_idx,
                train_start=str(min(tr_dates)),
                train_end=str(max(tr_dates)),
                val_start=str(min(va_dates)),
                val_end=str(max(va_dates)),
            )
        )

    dt_series = pd.to_datetime(df[date_col]).dt.date
    test_idx = df.index[dt_series.isin(test_dates)]
    return folds, test_idx
