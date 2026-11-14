"""Cloud Run Job: one-time bootstrap ETL.

Kaggle (1-min NSE) -> gs://.../raw/nse_750_1min -> split-adjusted gs://.../clean/equities_1min_adj
The bucket is mounted at LAKE_DIR (Cloud Storage FUSE), so no local disk is needed.
Safe to re-run: already-downloaded files are skipped.
"""

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd
import requests
import yfinance as yf

from src.data.corp_actions import adjust_1min_parquet
from src.data.validate_1min import validate_parquet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bootstrap_etl")

LAKE = Path(os.getenv("LAKE_DIR", "/lake"))
RAW = LAKE / "raw" / "nse_750_1min"
META = LAKE / "raw" / "nse_meta"
CLEAN = LAKE / "clean" / "equities_1min_adj"
REPORTS = LAKE / "reports"

DATASET = "nikhilkop/nse-750-clean-1min"
FILES = [
    "equities_1min.parquet",
    "indices_1min.parquet",
    "equities_75m.parquet",
    ".continuity_report.csv",
    "buyhold_2018_mfe_mae.csv",
    ".README_src.md",
]


def download_raw(token: str) -> None:
    """Stream each Kaggle file straight into the GCS-mounted raw/ folder."""
    RAW.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}"}
    for fname in FILES:
        out = RAW / fname
        url = f"https://www.kaggle.com/api/v1/datasets/download/{DATASET}/{fname}"
        with requests.get(url, headers=headers, stream=True, timeout=600) as r:
            r.raise_for_status()
            size = int(r.headers.get("Content-Length", 0))
            if out.exists() and size and out.stat().st_size == size:
                log.info("skip %s (already in lake, %.2f GB)", fname, size / 1e9)
                continue
            log.info("downloading %s (%.2f GB)", fname, size / 1e9)
            with open(out, "wb") as f:
                for chunk in r.iter_content(chunk_size=32 * 1024 * 1024):
                    f.write(chunk)


def fetch_splits(con: duckdb.DuckDBPyConnection) -> Path:
    """Fetch split/bonus history from Yahoo for every symbol in the dataset."""
    META.mkdir(parents=True, exist_ok=True)
    out = META / "splits.parquet"
    symbols = [r[0] for r in con.execute(
        f"SELECT DISTINCT symbol FROM read_parquet('{RAW / 'equities_1min.parquet'}')"
    ).fetchall()]
    rows, failed = [], 0
    for i, sym in enumerate(symbols, 1):
        try:
            splits = yf.Ticker(f"{sym}.NS").splits
            for dt, ratio in splits.items():
                rows.append({"symbol": sym, "ex_date": dt.strftime("%Y-%m-%d"), "split_ratio": float(ratio)})
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("splits failed for %s: %s", sym, exc)
        if i % 100 == 0:
            log.info("splits fetched for %d/%d symbols", i, len(symbols))
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["symbol", "ex_date", "split_ratio"]).astype(
        {"symbol": "string", "ex_date": "string", "split_ratio": "float64"}
    )
    df.to_parquet(out, index=False)
    log.info("splits: %d events for %d symbols (%d failed lookups)", len(df), len(symbols), failed)
    return out


def main() -> None:
    token = os.environ["KAGGLE_API_TOKEN"]
    started = datetime.now(UTC)

    download_raw(token)

    con = duckdb.connect()
    con.execute("SET TimeZone='Asia/Kolkata'")
    con.execute(f"SET memory_limit='{os.getenv('DUCKDB_MEMORY', '20GB')}'")
    con.execute("SET temp_directory='/tmp/duckdb'")
    con.execute("SET preserve_insertion_order=false")

    splits_path = fetch_splits(con)

    log.info("adjusting 1-min bars -> %s", CLEAN)
    adjust_1min_parquet(str(RAW / "equities_1min.parquet"), str(splits_path), str(CLEAN), con)

    log.info("validating adjusted output")
    report = validate_parquet(f"{CLEAN}/**/*.parquet")
    report["started_utc"] = started.isoformat()
    report["finished_utc"] = datetime.now(UTC).isoformat()

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "bootstrap_etl.json").write_text(json.dumps(report, indent=2, default=str))
    log.info("VALIDATION REPORT: %s", json.dumps(report, default=str))


if __name__ == "__main__":
    main()
