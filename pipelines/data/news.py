"""Whitelisted RSS news ingestion, universe entity matching, dedupe, and rule-based sentiment."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd

from libs.common.config import DATA_DIR, get_markets_config
from libs.common.db import get_conn
from libs.common.instruments import load_or_fetch_universe
from libs.common.logging import get_logger

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.data.news")

POS_WORDS = {
    "beat",
    "beats",
    "surge",
    "surges",
    "rally",
    "profit",
    "growth",
    "upgrade",
    "upgraded",
    "win",
    "wins",
    "order",
    "approval",
    "approved",
    "expansion",
    "record",
    "outperform",
    "buyback",
    "dividend",
}
NEG_WORDS = {
    "miss",
    "misses",
    "drop",
    "drops",
    "fall",
    "falls",
    "loss",
    "losses",
    "downgrade",
    "downgraded",
    "probe",
    "penalty",
    "fine",
    "default",
    "fraud",
    "ban",
    "warning",
    "slump",
    "plunge",
    "cut",
    "recall",
}


def score_financial_sentiment(text: str) -> float:
    """Deterministic lexicon sentiment in [-1.0, 1.0] (zero external LLM)."""
    tokens = re.findall(r"[a-z]+", text.lower())
    if not tokens:
        return 0.0
    pos = sum(1 for t in tokens if t in POS_WORDS)
    neg = sum(1 for t in tokens if t in NEG_WORDS)
    total = pos + neg
    return round((pos - neg) / total, 4) if total > 0 else 0.0


def _match_universe_symbols(text: str, universe_df: pd.DataFrame) -> list[tuple[str, str]]:
    """Return list of (isin, symbol) matched in text."""
    up = text.upper()
    matches: list[tuple[str, str]] = []
    for _, row in universe_df.iterrows():
        sym = str(row["symbol"]).upper()
        comp = str(row["company_name"]).upper().replace(" LTD.", "").replace(" LIMITED", "").strip()
        if re.search(rf"\b{re.escape(sym)}\b", up) or (len(comp) >= 5 and comp in up):
            matches.append((str(row["isin"]), str(row["symbol"])))
    return matches


def ingest_news(universe_df: pd.DataFrame | None = None) -> Path:
    cfg = get_markets_config()
    uni = universe_df if universe_df is not None else load_or_fetch_universe()
    sources = cfg.get("rss_sources", [])
    now_ist = datetime.now(IST)
    rows = []

    for src in sources:
        name = src["name"]
        url = src["url"]
        try:
            feed = feedparser.parse(url)
            for entry in getattr(feed, "entries", []):
                title = str(entry.get("title", "")).strip()
                summary = str(entry.get("summary", "")).strip()
                link = str(entry.get("link", "")).strip()
                pub_raw = entry.get("published") or entry.get("updated")
                try:
                    pub_dt = parsedate_to_datetime(pub_raw).astimezone(IST) if pub_raw else now_ist
                except Exception:  # noqa: BLE001
                    pub_dt = now_ist
                combined_text = f"{title}. {summary}"
                matched = _match_universe_symbols(combined_text, uni)
                if not matched:
                    continue
                sent = score_financial_sentiment(combined_text)
                item_hash = hashlib.sha256(f"{title}|{link}".encode()).hexdigest()[:16]
                for isin, sym in matched:
                    rows.append(
                        {
                            "news_id": item_hash,
                            "isin": isin,
                            "symbol": sym,
                            "source": name,
                            "title": title,
                            "link": link,
                            "published_at": pub_dt,
                            "known_as_of": max(pub_dt, now_ist),
                            "sentiment_score": sent,
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"RSS fetch failed for {name}: {exc}")

    out_dir = DATA_DIR / "raw" / "news"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "news_sentiment.parquet"
    new_df = pd.DataFrame(rows)
    if out_path.exists():
        prev = pd.read_parquet(out_path)
        if not new_df.empty:
            new_df = pd.concat([prev, new_df], ignore_index=True)
        else:
            new_df = prev
    if not new_df.empty:
        new_df = new_df.drop_duplicates(subset=["news_id", "isin"]).sort_values("published_at")
    else:
        new_df = pd.DataFrame(
            columns=[
                "news_id",
                "isin",
                "symbol",
                "source",
                "title",
                "link",
                "published_at",
                "known_as_of",
                "sentiment_score",
            ]
        )
    new_df.to_parquet(out_path, index=False)

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_sources_status (
                    source_name, status, coverage_start, coverage_end,
                    row_count, last_updated_ist, notes
                )
                VALUES ('news_rss', 'ok', %s, %s, %s, %s, %s)
                ON CONFLICT (source_name) DO UPDATE SET
                    status = EXCLUDED.status,
                    coverage_end = EXCLUDED.coverage_end,
                    row_count = EXCLUDED.row_count,
                    last_updated_ist = EXCLUDED.last_updated_ist,
                    notes = EXCLUDED.notes
                """,
                (
                    str(new_df["published_at"].min())[:10] if not new_df.empty else None,
                    str(new_df["published_at"].max())[:10] if not new_df.empty else None,
                    len(new_df),
                    now_ist,
                    f"Whitelisted RSS feeds ({len(sources)} sources), lexicon sentiment",
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"DB status skip for news_rss: {exc}")
    log.info(f"News store updated: {len(new_df)} universe-matched items")
    return out_path
