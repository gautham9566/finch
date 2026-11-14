"""Validate 1-minute NSE Parquet data without loading full file into RAM."""

import sys
import duckdb


def validate_parquet(parquet_path: str) -> dict:
    """Run core OHLCV integrity checks using DuckDB streaming."""
    con = duckdb.connect()
    query = f"""
    WITH base AS (
        SELECT
            symbol,
            timestamp_ist,
            open, high, low, close, volume,
            LAG(close) OVER (PARTITION BY symbol ORDER BY timestamp_ist) AS prev_close
        FROM read_parquet('{parquet_path}')
    )
    SELECT
        COUNT(*) AS total_rows,
        COUNT(DISTINCT symbol) AS n_symbols,
        MIN(timestamp_ist) AS min_ts,
        MAX(timestamp_ist) AS max_ts,
        SUM(CASE WHEN high < low OR open <= 0 OR close <= 0 THEN 1 ELSE 0 END) AS bad_ohlc,
        SUM(CASE WHEN volume = 0 THEN 1 ELSE 0 END) AS zero_vol_bars,
        SUM(CASE WHEN prev_close > 0 AND ABS(close / prev_close - 1) > 0.25 THEN 1 ELSE 0 END) AS jumps_over_25pct
    FROM base
    """
    row = con.execute(query).fetchone()
    cols = ["total_rows", "n_symbols", "min_ts", "max_ts", "bad_ohlc", "zero_vol_bars", "jumps_over_25pct"]
    return dict(zip(cols, row))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_1min.py <path-to-equities_1min.parquet>")
        sys.exit(1)
    for k, v in validate_parquet(sys.argv[1]).items():
        print(f"{k}: {v}")
