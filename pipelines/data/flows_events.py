"""FII/DII daily institutional flows and NSE corporate announcements / earnings calendar."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from libs.common.config import DATA_DIR
from libs.common.db import get_conn
from libs.common.logging import get_logger

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.flows_events")


def fetch_fii_dii_flows() -> pd.DataFrame:
    """Fetch latest NSE FII/DII trading activity from NSE public JSON endpoint."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/reports/fii-dii",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=15)
    resp = session.get("https://www.nseindia.com/api/fiidiiTradeReact", headers=headers, timeout=15)
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list) or not items:
        return pd.DataFrame(columns=["category", "date", "buyvalue", "sellvalue", "netvalue", "known_as_of", "source"])
    df = pd.DataFrame(items).copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    now_ist = datetime.now(IST)
    df["known_as_of"] = now_ist
    df["source"] = "nse_fiidii_api"
    return df


def fetch_nse_corporate_announcements() -> pd.DataFrame:
    """Fetch recent equity corporate announcements from NSE public JSON endpoint."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=15)
    resp = session.get(
        "https://www.nseindia.com/api/corporate-announcements?index=equities", headers=headers, timeout=15
    )
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list) or not items:
        return pd.DataFrame(columns=["symbol", "desc", "attchmntText", "an_dt", "known_as_of", "source"])
    rows = []
    now_ist = datetime.now(IST)
    for it in items:
        rows.append(
            {
                "symbol": str(it.get("symbol", "")).strip(),
                "company_name": str(it.get("sm_name", "")).strip(),
                "subject": str(it.get("desc", "")).strip(),
                "details": str(it.get("attchmntText", "")).strip(),
                "announced_at": str(it.get("an_dt", "")).strip(),
                "known_as_of": now_ist,
                "source": "nse_corporate_announcements",
            }
        )
    return pd.DataFrame(rows)


def ingest_flows_and_events() -> tuple[Path | None, Path | None]:
    out_dir = DATA_DIR / "raw" / "flows_events"
    out_dir.mkdir(parents=True, exist_ok=True)
    now_ist = datetime.now(IST)

    flows_path: Path | None = None
    try:
        fii_df = fetch_fii_dii_flows()
        flows_path = out_dir / "fii_dii.parquet"
        if flows_path.exists():
            prev = pd.read_parquet(flows_path)
            fii_df = pd.concat([prev, fii_df], ignore_index=True).drop_duplicates(
                subset=[c for c in ["category", "date"] if c in fii_df.columns] or None
            )
        fii_df.to_parquet(flows_path, index=False)
        _upsert_status("fii_dii_flows", "ok", len(fii_df), now_ist, "NSE FII/DII daily cash activity")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"FII/DII fetch failed: {exc}")
        _upsert_status("fii_dii_flows", "degraded", 0, now_ist, f"Fetch warning: {exc}")

    events_path: Path | None = None
    try:
        ev_df = fetch_nse_corporate_announcements()
        events_path = out_dir / "corporate_announcements.parquet"
        if events_path.exists() and not ev_df.empty:
            prev_ev = pd.read_parquet(events_path)
            ev_df = pd.concat([prev_ev, ev_df], ignore_index=True).drop_duplicates(
                subset=["symbol", "subject", "announced_at"]
            )
        ev_df.to_parquet(events_path, index=False)
        _upsert_status("corporate_events", "ok", len(ev_df), now_ist, "NSE corporate announcements & filings")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Corporate announcements fetch failed: {exc}")
        _upsert_status("corporate_events", "degraded", 0, now_ist, f"Fetch warning: {exc}")

    return flows_path, events_path


def _upsert_status(source: str, status: str, rows: int, ts: datetime, notes: str) -> None:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_sources_status (
                    source_name, status, coverage_start, coverage_end,
                    row_count, last_updated_ist, notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    coverage_end = EXCLUDED.coverage_end,
                    row_count = EXCLUDED.row_count,
                    last_updated_ist = EXCLUDED.last_updated_ist,
                    notes = EXCLUDED.notes
                """,
                (source, status, ts.date().isoformat(), ts.date().isoformat(), rows, ts, notes),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB status skip for {source}: {exc}")
