"""Instrument abstraction and Nifty 200 point-in-time universe management."""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from libs.common.config import DATA_DIR, get_markets_config
from libs.common.logging import get_logger
from libs.common.schemas import Instrument

log = get_logger("finch.instruments")


def fetch_nifty200_constituents(timeout: int = 20) -> pd.DataFrame:
    """Download official NSE Nifty 200 list (Company, Industry/Sector, Symbol, Series, ISIN)."""
    cfg = get_markets_config()
    url = cfg["nifty200_csv_url"]
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df = df.rename(
        columns={
            "Company Name": "company_name",
            "Industry": "sector",
            "Symbol": "symbol",
            "Series": "series",
            "ISIN Code": "isin",
        }
    )
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["isin"] = df["isin"].astype(str).str.strip()
    df["sector"] = df["sector"].astype(str).str.strip()
    df["company_name"] = df["company_name"].astype(str).str.strip()
    df = df[df["series"] == "EQ"].drop_duplicates(subset=["isin"]).reset_index(drop=True)
    # Track point-in-time membership window; historical additions/removals from NSE circulars override valid_from/to
    default_start = date.today() - timedelta(days=365 * int(cfg.get("history_years", 4)) + 30)
    df["valid_from"] = pd.Timestamp(default_start)
    df["valid_to"] = pd.NaT
    return df[["isin", "symbol", "company_name", "sector", "valid_from", "valid_to"]]


def load_or_fetch_universe(cache_path: Path | None = None, refresh: bool = False) -> pd.DataFrame:
    """Load cached universe Parquet or fetch fresh Nifty 200 constituents."""
    path = cache_path or (DATA_DIR / "clean" / "universe_nifty200.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = fetch_nifty200_constituents()
    df.to_parquet(path, index=False)
    log.info(f"Saved Nifty 200 universe ({len(df)} instruments) to {path}")
    return df


def active_instruments_as_of(universe_df: pd.DataFrame, as_of: date) -> list[Instrument]:
    """Filter universe to constituents active on `as_of` (prevents survivorship lookahead)."""
    ts = pd.Timestamp(as_of)
    mask = (pd.to_datetime(universe_df["valid_from"]) <= ts) & (
        universe_df["valid_to"].isna() | (pd.to_datetime(universe_df["valid_to"]) >= ts)
    )
    sub = universe_df[mask]
    return [
        Instrument(
            isin=str(r["isin"]),
            symbol=str(r["symbol"]),
            company_name=str(r["company_name"]),
            sector=str(r["sector"]),
            valid_from=pd.Timestamp(r["valid_from"]).date(),
            valid_to=pd.Timestamp(r["valid_to"]).date() if pd.notna(r["valid_to"]) else None,
        )
        for _, r in sub.iterrows()
    ]
