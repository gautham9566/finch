"""Indian daily equity prices (Nifty 200 + benchmarks), adjusted for splits/bonuses, keyed by ISIN and symbol."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

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

_YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
}
_THREAD_LOCAL = threading.local()


def _get_yahoo_session() -> requests.Session:
    sess = getattr(_THREAD_LOCAL, "session", None)
    if not isinstance(sess, requests.Session):
        sess = requests.Session()
        sess.headers.update(_YAHOO_HEADERS)
        _THREAD_LOCAL.session = sess
    return sess


def fetch_yahoo_chart_ohlcv(
    ticker: str,
    start_dt: date,
    end_dt: date,
    auto_adjust: bool = True,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch daily OHLCV directly from Yahoo Finance v8 chart JSON (bypasses broken crumb flow)."""
    period1 = int(datetime(start_dt.year, start_dt.month, start_dt.day, tzinfo=UTC).timestamp())
    period2 = int(datetime(end_dt.year, end_dt.month, end_dt.day, tzinfo=UTC).timestamp())
    encoded = quote(ticker, safe="")
    params: dict[str, str | int] = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "div,split",
    }
    http = session or _get_yahoo_session()

    payload: dict[str, object] | None = None
    last_status: int | None = None
    for attempt in range(4):
        host = _YAHOO_HOSTS[attempt % len(_YAHOO_HOSTS)]
        url = f"https://{host}/v8/finance/chart/{encoded}"
        try:
            resp = http.get(url, params=params, timeout=timeout)
            last_status = resp.status_code
            if resp.status_code == 404:
                log.warning(f"Yahoo chart 404 for {ticker}")
                return pd.DataFrame()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.6 * (attempt + 1))
                continue
            resp.raise_for_status()
            payload = resp.json()
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt == 3:
                log.warning(f"Yahoo chart failed for {ticker}: {exc}")
                return pd.DataFrame()
            time.sleep(0.6 * (attempt + 1))

    if payload is None:
        log.warning(f"Yahoo chart gave no payload for {ticker} (last HTTP {last_status})")
        return pd.DataFrame()

    if not payload or not isinstance(payload, dict):
        return pd.DataFrame()
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return pd.DataFrame()
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        err = chart.get("error")
        if err:
            log.warning(f"Yahoo chart error for {ticker}: {err}")
        return pd.DataFrame()

    res = results[0]
    timestamps = res.get("timestamp") or []
    indicators = res.get("indicators") or {}
    quote_list = indicators.get("quote") or []
    if not timestamps or not quote_list or not isinstance(quote_list[0], dict):
        return pd.DataFrame()
    q = quote_list[0]

    tz_name = str((res.get("meta") or {}).get("exchangeTimezoneName") or "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")

    trade_dates = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(tz).date
    df = pd.DataFrame(
        {
            "trade_date": trade_dates,
            "open": pd.to_numeric(pd.Series(q.get("open", [None] * len(timestamps))), errors="coerce"),
            "high": pd.to_numeric(pd.Series(q.get("high", [None] * len(timestamps))), errors="coerce"),
            "low": pd.to_numeric(pd.Series(q.get("low", [None] * len(timestamps))), errors="coerce"),
            "close": pd.to_numeric(pd.Series(q.get("close", [None] * len(timestamps))), errors="coerce"),
            "volume": pd.to_numeric(pd.Series(q.get("volume", [0] * len(timestamps))), errors="coerce")
            .fillna(0)
            .astype("int64"),
        }
    )

    adj_list = indicators.get("adjclose") or []
    adj_vals = adj_list[0].get("adjclose") if adj_list and isinstance(adj_list[0], dict) else None
    if auto_adjust and isinstance(adj_vals, list) and len(adj_vals) == len(timestamps):
        df["adj_close"] = pd.to_numeric(pd.Series(adj_vals), errors="coerce")
    else:
        df["adj_close"] = pd.Series(float("nan"), index=df.index, dtype="float64")

    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    if df.empty:
        return pd.DataFrame()

    if auto_adjust:
        valid = df["adj_close"].notna() & (df["close"] > 0)
        if valid.any():
            factor = pd.Series(1.0, index=df.index, dtype="float64")
            factor.loc[valid] = df.loc[valid, "adj_close"] / df.loc[valid, "close"]
            for col in ("open", "high", "low", "close"):
                df[col] = (df[col] * factor).astype("float64")

    df = (
        df[["trade_date", "open", "high", "low", "close", "volume"]]
        .sort_values("trade_date")
        .drop_duplicates(subset=["trade_date"], keep="last")
        .reset_index(drop=True)
    )
    return df


def fetch_daily_equities(universe_df: pd.DataFrame, years: int = 4, batch_size: int = 40) -> pd.DataFrame:
    """Download multi-year split/dividend-adjusted NSE daily OHLCV keyed by ISIN and symbol."""
    end_dt = date.today() + timedelta(days=1)
    start_dt = date.today() - timedelta(days=365 * years + 45)
    isin_map = dict(zip(universe_df["symbol"], universe_df["isin"], strict=True))
    sector_map = dict(zip(universe_df["symbol"], universe_df["sector"], strict=True))

    symbols = list(universe_df["symbol"].unique())
    records: list[pd.DataFrame] = []
    _ = batch_size

    def _fetch_one(sym: str) -> pd.DataFrame | None:
        time.sleep(0.05)
        sub = fetch_yahoo_chart_ohlcv(f"{sym}.NS", start_dt, end_dt, auto_adjust=True)
        if sub.empty:
            return None
        out_sub = pd.DataFrame(
            {
                "isin": isin_map[sym],
                "symbol": sym,
                "sector": sector_map[sym],
                "trade_date": sub["trade_date"],
                "open": sub["open"].astype(float),
                "high": sub["high"].astype(float),
                "low": sub["low"].astype(float),
                "close": sub["close"].astype(float),
                "volume": sub["volume"].astype("int64"),
            }
        )
        out_sub["traded_value_inr"] = out_sub["close"] * out_sub["volume"]
        # EOD bar is only known after NSE close (16:00 IST on that trade_date)
        out_sub["known_as_of"] = pd.to_datetime(out_sub["trade_date"].astype(str) + " 16:00:00+05:30")
        out_sub["source"] = "yahoo_chart_nse_adj"
        return out_sub

    log.info(f"Fetching daily OHLCV for {len(symbols)} NSE symbols via Yahoo chart JSON")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_fetch_one, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            df_sym = fut.result()
            if df_sym is not None and not df_sym.empty:
                records.append(df_sym)

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
        hist = fetch_yahoo_chart_ohlcv(ticker, start_dt, end_dt, auto_adjust=False)
        if hist.empty:
            continue
        df = pd.DataFrame(
            {
                "symbol": name,
                "ticker": ticker,
                "trade_date": hist["trade_date"],
                "open": hist["open"].astype(float),
                "high": hist["high"].astype(float),
                "low": hist["low"].astype(float),
                "close": hist["close"].astype(float),
                "volume": hist["volume"].astype("int64"),
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
