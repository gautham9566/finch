"""Indian daily equity prices (Nifty 200 + benchmarks), adjusted for splits/bonuses, keyed by ISIN and symbol."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from libs.common.config import DATA_DIR, get_markets_config
from libs.common.db import get_conn
from libs.common.instruments import load_or_fetch_universe
from libs.common.logging import get_logger

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.sources")

INDEX_TICKERS = {
    "^NSEI": "NIFTY50",
    "^NSEBANK": "NIFTYBANK",
}


def fetch_daily_equities(universe_df: pd.DataFrame, years: int = 4, batch_size: int = 40) -> pd.DataFrame:
    """Download multi-year split/dividend-adjusted NSE daily OHLCV keyed by ISIN and symbol."""
    end_dt = date.today() + timedelta(days=1)
    start_dt = date.today() - timedelta(days=365 * years + 45)
    isin_map = dict(zip(universe_df["symbol"], universe_df["isin"], strict=True))
    sector_map = dict(zip(universe_df["symbol"], universe_df["sector"], strict=True))

    symbols = list(universe_df["symbol"].unique())
    records: list[pd.DataFrame] = []

    for idx in range(0, len(symbols), batch_size):
        chunk = symbols[idx : idx + batch_size]
        yf_tickers = [f"{s}.NS" for s in chunk]
        log.info(f"Fetching daily OHLCV batch {idx // batch_size + 1}/{(len(symbols) - 1) // batch_size + 1}")
        raw = yf.download(
            tickers=yf_tickers,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        if raw.empty:
            continue
        for sym in chunk:
            yt = f"{sym}.NS"
            try:
                sub = raw[yt].copy() if isinstance(raw.columns, pd.MultiIndex) else raw.copy()
            except KeyError:
                continue
            sub = sub.dropna(subset=["Open", "High", "Low", "Close"]).reset_index()
            if sub.empty:
                continue
            dt_col = "Date" if "Date" in sub.columns else sub.columns[0]
            sub = pd.DataFrame(
                {
                    "isin": isin_map[sym],
                    "symbol": sym,
                    "sector": sector_map[sym],
                    "trade_date": pd.to_datetime(sub[dt_col]).dt.date,
                    "open": sub["Open"].astype(float),
                    "high": sub["High"].astype(float),
                    "low": sub["Low"].astype(float),
                    "close": sub["Close"].astype(float),
                    "volume": sub["Volume"].fillna(0).astype("int64"),
                }
            )
            sub["traded_value_inr"] = sub["close"] * sub["volume"]
            # EOD bar is only known after NSE close (16:00 IST on that trade_date)
            sub["known_as_of"] = pd.to_datetime(sub["trade_date"].astype(str) + " 16:00:00+05:30")
            sub["source"] = "yfinance_nse_adj"
            records.append(sub)

    if not records:
        raise RuntimeError("No Indian daily equity rows fetched")
    out = pd.concat(records, ignore_index=True)
    out = (
        out.sort_values(["trade_date", "symbol"]).drop_duplicates(subset=["trade_date", "isin"]).reset_index(drop=True)
    )
    return out


def fetch_benchmark_indices(years: int = 4) -> pd.DataFrame:
    """Fetch Nifty 50 and Nifty Bank daily series for relative strength and benchmark comparisons."""
    end_dt = date.today() + timedelta(days=1)
    start_dt = date.today() - timedelta(days=365 * years + 45)
    frames = []
    for ticker, name in INDEX_TICKERS.items():
        hist = (
            yf.Ticker(ticker).history(start=start_dt.isoformat(), end=end_dt.isoformat(), interval="1d").reset_index()
        )
        if hist.empty:
            continue
        dt_col = "Date" if "Date" in hist.columns else hist.columns[0]
        df = pd.DataFrame(
            {
                "symbol": name,
                "ticker": ticker,
                "trade_date": pd.to_datetime(hist[dt_col]).dt.date,
                "open": hist["Open"].astype(float),
                "high": hist["High"].astype(float),
                "low": hist["Low"].astype(float),
                "close": hist["Close"].astype(float),
                "volume": hist["Volume"].fillna(0).astype("int64"),
            }
        )
        df["known_as_of"] = pd.to_datetime(df["trade_date"].astype(str) + " 16:00:00+05:30")
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def ingest_indian_daily(refresh_universe: bool = True) -> tuple[Path, Path]:
    """Fetch universe, daily prices, and benchmark indices into raw Parquet and record source status."""
    cfg = get_markets_config()
    years = int(cfg.get("history_years", 4))
    universe = load_or_fetch_universe(refresh=refresh_universe)
    daily_df = fetch_daily_equities(universe, years=years)
    bench_df = fetch_benchmark_indices(years=years)

    raw_dir = DATA_DIR / "raw" / "daily_prices"
    raw_dir.mkdir(parents=True, exist_ok=True)
    eq_path = raw_dir / "nse_nifty200_daily.parquet"
    bench_path = raw_dir / "nse_benchmarks_daily.parquet"
    daily_df.to_parquet(eq_path, index=False)
    bench_df.to_parquet(bench_path, index=False)

    now_ist = datetime.now(IST)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_sources_status (
                    source_name, status, coverage_start, coverage_end,
                    row_count, last_updated_ist, notes
                )
                VALUES ('indian_daily_prices', 'ok', %s, %s, %s, %s, %s)
                ON CONFLICT (source_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    coverage_start = EXCLUDED.coverage_start,
                    coverage_end = EXCLUDED.coverage_end,
                    row_count = EXCLUDED.row_count,
                    last_updated_ist = EXCLUDED.last_updated_ist,
                    notes = EXCLUDED.notes
                """,
                (
                    str(daily_df["trade_date"].min()),
                    str(daily_df["trade_date"].max()),
                    len(daily_df),
                    now_ist,
                    f"{daily_df['isin'].nunique()} ISINs adjusted for splits/dividends",
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB update skipped for indian_daily_prices: {exc}")
    log.info(f"Ingested {len(daily_df)} daily rows ({daily_df['trade_date'].min()} -> {daily_df['trade_date'].max()})")
    return eq_path, bench_path
