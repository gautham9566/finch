"""Unit tests for Phase D1/D2 validation and corporate action math."""

from pathlib import Path
import polars as pl
from src.data.validate_1min import validate_parquet
from src.data.corp_actions import compute_cumulative_factors


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
    factors = dict((dt, fac) for _, dt, fac in compute_cumulative_factors(events))
    assert factors["2018-09-04"] == 2.0
    assert factors["2015-06-15"] == 4.0
