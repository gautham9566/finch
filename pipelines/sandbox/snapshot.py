"""Pre-market snapshot freezer ensuring exact train/serve point-in-time parity."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from libs.common.config import DATA_DIR
from pipelines.data.features import assert_no_leakage

IST = ZoneInfo("Asia/Kolkata")


def freeze_snapshot_for_date(trade_date: date, slot: str = "09:20") -> Path:
    """Slice feature store strictly to `trade_date`, stamp slot receipt time, and freeze to Parquet."""
    feat_path = DATA_DIR / "features" / "features_daily.parquet"
    df = pd.read_parquet(feat_path)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    snap = df[df["trade_date"] == trade_date].copy()
    if snap.empty:
        raise ValueError(f"No feature rows available to freeze snapshot for {trade_date}")

    snap["snapshot_slot"] = slot
    snap["snapshot_frozen_at"] = datetime.now(IST)
    assert_no_leakage(snap)

    snap_dir = DATA_DIR / "journal" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    out = snap_dir / f"snapshot_{trade_date.isoformat()}_{slot.replace(':', '')}.parquet"
    snap.to_parquet(out, index=False)
    return out
