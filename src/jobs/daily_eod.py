"""Cloud Run Job: daily end-of-day refresh (scheduled 18:30 IST, Mon-Fri).

Writes to the GCS-mounted lake:
  raw/nse_bhavcopy/date=YYYY-MM-DD/bhav.parquet   (price, volume, delivery %)
  raw/nse_fii_dii/date=YYYY-MM-DD/fii_dii.parquet
  raw/macro/date=YYYY-MM-DD/macro.parquet         (global indices, FX, crude, VIX)
Each source is independent: one failing (e.g. holiday) does not stop the others.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from nselib import capital_market

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("daily_eod")

LAKE = Path(os.getenv("LAKE_DIR", "/lake"))
MACRO_TICKERS = ["^NSEI", "^NSEBANK", "^INDIAVIX", "^GSPC", "^IXIC", "^N225", "^HSI", "DX-Y.NYB", "BZ=F", "INR=X"]


def _write(df: pd.DataFrame, source: str, day: str, name: str) -> int:
    out = LAKE / "raw" / source / f"date={day}"
    out.mkdir(parents=True, exist_ok=True)
    df.columns = [str(c) for c in df.columns]
    df.to_parquet(out / f"{name}.parquet", index=False)
    return len(df)


def fetch_bhavcopy(day_ddmmyyyy: str) -> pd.DataFrame:
    return capital_market.bhav_copy_with_delivery(trade_date=day_ddmmyyyy)


def fetch_fii_dii() -> pd.DataFrame:
    return capital_market.fii_dii_trading_activity()


def fetch_macro() -> pd.DataFrame:
    frames = []
    for t in MACRO_TICKERS:
        hist = yf.Ticker(t).history(period="5d", interval="1d").reset_index()
        hist["ticker"] = t
        frames.append(hist)
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    day = now.strftime("%Y-%m-%d")
    tasks = {
        "nse_bhavcopy": ("bhav", lambda: fetch_bhavcopy(now.strftime("%d-%m-%Y"))),
        "nse_fii_dii": ("fii_dii", fetch_fii_dii),
        "macro": ("macro", fetch_macro),
    }
    report: dict[str, str | int] = {"date": day}
    for source, (name, fn) in tasks.items():
        try:
            report[source] = _write(fn(), source, day, name)
            log.info("%s: %s rows", source, report[source])
        except Exception as exc:  # noqa: BLE001
            report[source] = f"FAILED: {exc}"
            log.error("%s failed: %s", source, exc)

    out = LAKE / "reports" / "daily_eod"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{day}.json").write_text(json.dumps(report, indent=2))
    log.info("REPORT: %s", json.dumps(report))

    if all(isinstance(v, str) and v.startswith("FAILED") for k, v in report.items() if k != "date"):
        sys.exit(1)


if __name__ == "__main__":
    main()
