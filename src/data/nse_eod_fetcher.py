"""Fetch NSE daily EOD data, FII/DII activity, and corporate actions."""

import os
from datetime import datetime, timedelta
import polars as pl
import yfinance as yf
from nselib import capital_market


def fetch_fii_dii(out_path: str) -> int:
    """Fetch recent FII/DII trading activity from NSE via nselib."""
    df = capital_market.fii_dii_trading_activity()
    pl_df = pl.from_pandas(df)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pl_df.write_parquet(out_path)
    return pl_df.height


def fetch_eod_delivery(symbol: str, days: int = 365) -> pl.DataFrame:
    """Fetch daily price, volume, and delivery % for an NSE symbol."""
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=days)
    df = capital_market.price_volume_and_deliverable_position_data(
        symbol=symbol,
        from_date=from_dt.strftime("%d-%m-%Y"),
        to_date=to_dt.strftime("%d-%m-%Y"),
    )
    return pl.from_pandas(df)


def fetch_splits_yfinance(symbols: list[str], out_path: str) -> int:
    """Fetch stock split history from Yahoo Finance for backward price adjustment."""
    rows = []
    for sym in symbols:
        splits = yf.Ticker(f"{sym}.NS").splits
        if splits is not None and not splits.empty:
            for dt, ratio in splits.items():
                rows.append({"symbol": sym, "ex_date": dt.strftime("%Y-%m-%d"), "split_ratio": float(ratio)})
    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={"symbol": pl.Utf8, "ex_date": pl.Utf8, "split_ratio": pl.Float64})
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.write_parquet(out_path)
    return df.height
