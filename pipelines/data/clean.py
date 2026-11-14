"""Automated noise removal and point-in-time cleaning for daily prices and global cues."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from libs.common.config import DATA_DIR, get_markets_config
from libs.common.instruments import load_or_fetch_universe
from libs.common.logging import get_logger

log = get_logger("finch.data.clean")


def clean_daily_prices(
    raw_path: Path | None = None, universe_df: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Deduplicate, validate OHLC consistency, flag circuits, and filter illiquid names."""
    cfg = get_markets_config()
    src = raw_path or (DATA_DIR / "raw" / "daily_prices" / "nse_nifty200_daily.parquet")
    df = pd.read_parquet(src)
    uni = universe_df if universe_df is not None else load_or_fetch_universe()

    # 1. Deduplicate by (trade_date, isin)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df = df.sort_values(["isin", "trade_date", "known_as_of"]).drop_duplicates(
        subset=["isin", "trade_date"], keep="last"
    )

    # 2. Remove weekends and configured NSE holidays (never blind-fill holidays)
    holidays = {pd.to_datetime(h).date() for h in cfg.get("nse_holidays", [])}
    dt_series = pd.to_datetime(df["trade_date"])
    df = df[(dt_series.dt.dayofweek < 5) & (~df["trade_date"].isin(holidays))].copy()

    # 3. Validate OHLC consistency
    valid_ohlc = (
        (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
        & (df["high"] >= df["low"])
        & (df["high"] >= df["open"])
        & (df["high"] >= df["close"])
        & (df["low"] <= df["open"])
        & (df["low"] <= df["close"])
        & (df["volume"] >= 0)
    )
    bad_ohlc_count = int((~valid_ohlc).sum())
    df = df[valid_ohlc].copy()

    # 4. Enforce point-in-time universe membership
    uni_win = uni[["isin", "valid_from", "valid_to"]].copy()
    df = df.merge(uni_win, on="isin", how="inner")
    td = pd.to_datetime(df["trade_date"])
    in_univ = (td >= pd.to_datetime(df["valid_from"])) & (
        df["valid_to"].isna() | (td <= pd.to_datetime(df["valid_to"]))
    )
    df = df[in_univ].drop(columns=["valid_from", "valid_to"])

    # 5. Flag circuit-limit / extreme price jump days and drop illiquid names
    df = df.sort_values(["isin", "trade_date"]).reset_index(drop=True)
    df["prev_close"] = df.groupby("isin")["close"].shift(1)
    df["ret_1d"] = (df["close"] / df["prev_close"]) - 1.0
    max_move = float(cfg.get("max_circuit_move_pct", 0.20))
    df["is_circuit_or_jump"] = df["ret_1d"].abs() >= max_move

    df["median_tv_20d"] = df.groupby("isin")["traded_value_inr"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=5).median()
    )
    min_tv = float(cfg.get("min_median_traded_value_inr", 50_000_000.0))
    # Keep early rows (first 5 bars before rolling window warms up) or liquid bars
    liquid_mask = df["median_tv_20d"].isna() | (df["median_tv_20d"] >= min_tv)
    illiquid_dropped = int((~liquid_mask).sum())
    df = df[liquid_mask].drop(columns=["prev_close", "ret_1d", "median_tv_20d"]).reset_index(drop=True)

    # Ensure known_as_of column is timezone-aware IST
    df["known_as_of"] = pd.to_datetime(df["known_as_of"], utc=True).dt.tz_convert("Asia/Kolkata")

    clean_dir = DATA_DIR / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    out_path = clean_dir / "equities_daily_clean.parquet"
    df.to_parquet(out_path, index=False)

    stats = {
        "clean_rows": len(df),
        "bad_ohlc_removed": bad_ohlc_count,
        "illiquid_dropped": illiquid_dropped,
        "circuit_flagged": int(df["is_circuit_or_jump"].sum()),
    }
    log.info(f"Cleaned daily prices: {stats}")
    return df, stats


def clean_global_cues(raw_path: Path | None = None) -> pd.DataFrame:
    """Deduplicate and compute 1-day returns per global cue series without cross-country forward-fill."""
    src = raw_path or (DATA_DIR / "raw" / "global_cues" / "global_cues_daily.parquet")
    df = pd.read_parquet(src)
    df["obs_date"] = pd.to_datetime(df["obs_date"]).dt.date
    df = df.sort_values(["symbol", "obs_date"]).drop_duplicates(subset=["symbol", "obs_date"], keep="last")
    df = df[np.isfinite(df["close"]) & (df["close"] > 0)].copy()
    df["ret_1d"] = df.groupby("symbol")["close"].pct_change().fillna(0.0)
    df["known_as_of"] = pd.to_datetime(df["known_as_of"], utc=True).dt.tz_convert("Asia/Kolkata")

    out_path = DATA_DIR / "clean" / "global_cues_clean.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return df
