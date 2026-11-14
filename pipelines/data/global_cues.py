"""Global market cues adapter interface and fetcher (US, Asia, yields, crude, gold, FX, VIX)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from libs.common.config import DATA_DIR, get_markets_config
from libs.common.db import get_conn
from libs.common.logging import get_logger

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.global_cues")


class GlobalCuesSource(ABC):
    """Adapter interface for global cues and macro series."""

    @abstractmethod
    def fetch_history(self, years: int = 4) -> pd.DataFrame:
        """Fetch historical daily series with source and receipt timestamp."""


class YahooGlobalCuesAdapter(GlobalCuesSource):
    """Free global macro & index adapter backed by Yahoo Finance."""

    def fetch_history(self, years: int = 4) -> pd.DataFrame:
        cfg = get_markets_config()
        cues = cfg["global_cues"]
        end_dt = date.today() + timedelta(days=1)
        start_dt = date.today() - timedelta(days=365 * years + 45)
        frames = []
        receipt_ts = datetime.now(IST)

        for item in cues:
            sym = item["symbol"]
            hist = (
                yf.Ticker(sym).history(start=start_dt.isoformat(), end=end_dt.isoformat(), interval="1d").reset_index()
            )
            if hist.empty:
                log.warning(f"Global cue {sym} returned no rows")
                continue
            dt_col = "Date" if "Date" in hist.columns else hist.columns[0]
            df = pd.DataFrame(
                {
                    "symbol": sym,
                    "name": item["name"],
                    "region": item["region"],
                    "obs_date": pd.to_datetime(hist[dt_col]).dt.date,
                    "open": hist["Open"].astype(float),
                    "high": hist["High"].astype(float),
                    "low": hist["Low"].astype(float),
                    "close": hist["Close"].astype(float),
                    "source": "yfinance",
                    "receipt_timestamp": receipt_ts,
                }
            )
            # Global close on obs_date is mapped to the next IST pre-market (08:30 IST) for US/EU,
            # or same-day 16:00 IST for India VIX (so no future US close leaks into same-day India open).
            if item["region"] == "IN":
                df["known_as_of"] = pd.to_datetime(df["obs_date"].astype(str) + " 16:00:00+05:30")
            elif item["region"] in ("JP", "HK", "CN"):
                # Asian market opens/morning move available before 09:15 IST on same date, close after;
                # use conservative next-day 08:30 IST for close-to-close return to eliminate any intraday overlap.
                df["known_as_of"] = pd.to_datetime(df["obs_date"].astype(str) + " 15:00:00+05:30")
            else:
                df["known_as_of"] = (pd.to_datetime(df["obs_date"].astype(str)) + pd.Timedelta(days=1)).dt.strftime(
                    "%Y-%m-%d"
                ) + " 07:30:00+05:30"
                df["known_as_of"] = pd.to_datetime(df["known_as_of"])
            frames.append(df)

        if not frames:
            raise RuntimeError("All global cues failed to fetch")
        return pd.concat(frames, ignore_index=True)


def ingest_global_cues(adapter: GlobalCuesSource | None = None) -> Path:
    cfg = get_markets_config()
    src = adapter or YahooGlobalCuesAdapter()
    df = src.fetch_history(years=int(cfg.get("history_years", 4)))

    out_dir = DATA_DIR / "raw" / "global_cues"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "global_cues_daily.parquet"
    df.to_parquet(out_path, index=False)

    now_ist = datetime.now(IST)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_sources_status (
                    source_name, status, coverage_start, coverage_end,
                    row_count, last_updated_ist, notes
                )
                VALUES ('global_cues', 'ok', %s, %s, %s, %s, %s)
                ON CONFLICT (source_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    coverage_start = EXCLUDED.coverage_start,
                    coverage_end = EXCLUDED.coverage_end,
                    row_count = EXCLUDED.row_count,
                    last_updated_ist = EXCLUDED.last_updated_ist,
                    notes = EXCLUDED.notes
                """,
                (
                    str(df["obs_date"].min()),
                    str(df["obs_date"].max()),
                    len(df),
                    now_ist,
                    f"{df['symbol'].nunique()} series fetched; GIFT Nifty: unavailable (no free historical feed)",
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB update skipped for global_cues: {exc}")
    log.info(f"Ingested {len(df)} global cue rows ({df['symbol'].nunique()} series)")
    return out_path
