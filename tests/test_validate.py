"""Unit tests for Phase D1/D2 validation and corporate action math."""

from pathlib import Path

import duckdb
import polars as pl

from src.data.corp_actions import adjust_1min_parquet, compute_cumulative_factors
from src.data.validate_1min import validate_parquet


def test_validate_parquet(tmp_path: Path) -> None:
    p = tmp_path / "sample.parquet"
    df = pl.DataFrame({
        "symbol": ["RELIANCE", "RELIANCE", "TCS"],
        "timestamp_ist": ["2025-01-02 09:15:00", "2025-01-02 09:16:00", "2025-01-02 09:15:00"],
        "open": [100.0, 101.0, 200.0],
        "high": [102.0, 100.0, 205.0],  # 2nd row has high < low (bad_ohlc=1)
        "low": [99.0, 102.0, 198.0],
        "close": [101.0, 101.5, 202.0],
        "volume": [1000, 0, 500],
    })
    df.write_parquet(p)
    res = validate_parquet(str(p))
    assert res["total_rows"] == 3
    assert res["n_symbols"] == 2
    assert res["bad_ohlc"] == 1
    assert res["zero_vol_bars"] == 1


def test_compute_cumulative_factors() -> None:
    events = [("INFY", "2018-09-04", 2.0), ("INFY", "2015-06-15", 2.0)]
    factors = {dt: fac for _, dt, fac in compute_cumulative_factors(events)}
    assert factors["2018-09-04"] == 2.0
    assert factors["2015-06-15"] == 4.0


def test_adjust_1min_parquet_two_splits(tmp_path: Path) -> None:
    raw = tmp_path / "raw.parquet"
    splits = tmp_path / "splits.parquet"
    out = tmp_path / "out"
    pl.DataFrame({
        "symbol": ["INFY", "INFY", "INFY", "TCS"],
        "timestamp_ist": ["2015-06-12 09:15:00", "2018-09-03 09:15:00", "2018-09-04 09:15:00", "2018-09-03 09:15:00"],
        "open": [4000.0, 1400.0, 700.0, 2000.0],
        "high": [4000.0, 1400.0, 700.0, 2000.0],
        "low": [4000.0, 1400.0, 700.0, 2000.0],
        "close": [4000.0, 1400.0, 700.0, 2000.0],
        "volume": [10, 10, 10, 10],
    }).write_parquet(raw)
    pl.DataFrame({
        "symbol": ["INFY", "INFY"],
        "ex_date": ["2015-06-15", "2018-09-04"],
        "split_ratio": [2.0, 2.0],
    }).write_parquet(splits)

    adjust_1min_parquet(str(raw), str(splits), str(out))
    rows = duckdb.sql(
        f"SELECT symbol, CAST(timestamp_ist AS DATE)::VARCHAR, close, volume "
        f"FROM read_parquet('{out}/**/*.parquet', hive_partitioning=true) ORDER BY symbol, timestamp_ist"
    ).fetchall()
    assert rows == [
        ("INFY", "2015-06-12", 1000.0, 40),  # before both splits: /4
        ("INFY", "2018-09-03", 700.0, 20),   # before 2nd split: /2
        ("INFY", "2018-09-04", 700.0, 10),   # on/after last split: unchanged
        ("TCS", "2018-09-03", 2000.0, 10),   # no splits: unchanged
    ]
