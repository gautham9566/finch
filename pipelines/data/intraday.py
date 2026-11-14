"""Intraday 1/5-minute data source abstraction and broker adapter (TODO: add live broker key later)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from libs.common.config import DATA_DIR
from libs.common.db import get_conn
from libs.common.logging import get_logger
from libs.common.storage import get_secret

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.intraday")


class IntradayDataSource(ABC):
    """Abstract interface for 1-minute or 5-minute NSE intraday candles."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if broker API credentials are present."""

    @abstractmethod
    def fetch_intraday_bars(self, symbol: str, isin: str, trade_date: date, interval_min: int = 5) -> pd.DataFrame:
        """Fetch intraday OHLCV bars for one instrument and date."""


class BrokerIntradayAdapter(IntradayDataSource):
    """Working DhanHQ-compatible intraday adapter reading its API key from Secret Manager.

    TODO: User will add the broker API key to GCP Secret Manager secret `finch-intraday-api-key`
    (format: `<client_id>:<access_token>`) when ready to enable live/historical 1m/5m bars.
    """

    SECRET_ID = "finch-intraday-api-key"
    ENDPOINT = "https://api.dhan.co/v2/charts/intraday"

    def __init__(self, secret_value: str | None = None) -> None:
        self._secret = secret_value if secret_value is not None else get_secret(self.SECRET_ID)

    def is_configured(self) -> bool:
        return bool(self._secret and ":" in self._secret)

    def fetch_intraday_bars(self, symbol: str, isin: str, trade_date: date, interval_min: int = 5) -> pd.DataFrame:
        if not self.is_configured():
            return pd.DataFrame(
                columns=["isin", "symbol", "timestamp_ist", "open", "high", "low", "close", "volume", "known_as_of"]
            )
        assert self._secret is not None
        client_id, access_token = self._secret.split(":", 1)
        payload = {
            "securityId": symbol,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": str(interval_min),
            "fromDate": f"{trade_date.isoformat()} 09:15:00",
            "toDate": f"{trade_date.isoformat()} 15:30:00",
        }
        resp = requests.post(
            self.ENDPOINT,
            headers={"access-token": access_token, "client-id": client_id, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        ts_list = data.get("timestamp", [])
        if not ts_list:
            return pd.DataFrame()
        df = pd.DataFrame(
            {
                "isin": isin,
                "symbol": symbol,
                "timestamp_ist": pd.to_datetime(ts_list, unit="s", utc=True).tz_convert(IST),
                "open": data["open"],
                "high": data["high"],
                "low": data["low"],
                "close": data["close"],
                "volume": data["volume"],
            }
        )
        df["known_as_of"] = df["timestamp_ist"] + pd.Timedelta(minutes=interval_min)
        return df


def record_intraday_status(adapter: IntradayDataSource) -> str:
    """Persist intraday adapter configuration state in Postgres and return status string."""
    now_ist = datetime.now(IST)
    if not adapter.is_configured():
        status = "intraday: not configured"
        notes = "Secret 'finch-intraday-api-key' has no version yet (TODO: enable broker key later); running on daily."
    else:
        status = "intraday: configured"
        notes = "Broker adapter credentials loaded from Secret Manager."

    # Check forward intraday storage if any exists
    intraday_dir = DATA_DIR / "raw" / "intraday"
    files = list(intraday_dir.glob("*.parquet")) if intraday_dir.exists() else []
    row_count = sum(len(pd.read_parquet(f)) for f in files) if files else 0

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_sources_status (
                    source_name, status, coverage_start, coverage_end,
                    row_count, last_updated_ist, notes
                )
                VALUES ('intraday_1m_5m', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    row_count = EXCLUDED.row_count,
                    last_updated_ist = EXCLUDED.last_updated_ist,
                    notes = EXCLUDED.notes
                """,
                (status, None, None, row_count, now_ist, notes),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Could not persist intraday status to Postgres: {exc}")
    log.info(f"Intraday adapter status: {status}")
    return status


def save_forward_intraday(trade_date: date, universe_df: pd.DataFrame) -> int:
    """Daily forward collector: saves intraday bars once broker secret is populated."""
    adapter = BrokerIntradayAdapter()
    record_intraday_status(adapter)
    if not adapter.is_configured():
        return 0
    frames = []
    for _, row in universe_df.iterrows():
        try:
            df = adapter.fetch_intraday_bars(str(row["symbol"]), str(row["isin"]), trade_date)
            if not df.empty:
                frames.append(df)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"Intraday fetch failed for {row['symbol']}: {exc}")
    if not frames:
        return 0
    out_dir = DATA_DIR / "raw" / "intraday"
    out_dir.mkdir(parents=True, exist_ok=True)
    combined = pd.concat(frames, ignore_index=True)
    combined.to_parquet(out_dir / f"intraday_{trade_date.isoformat()}.parquet", index=False)
    return len(combined)
