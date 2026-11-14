"""Shared Pydantic schemas enforcing point-in-time timestamps and clean contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Instrument(BaseModel):
    isin: str
    symbol: str
    company_name: str
    sector: str
    exchange: str = "NSE"
    valid_from: date
    valid_to: date | None = None


class OHLCVBar(BaseModel):
    isin: str
    symbol: str
    trade_date: date
    timestamp_ist: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    traded_value_inr: float
    adj_factor: float = 1.0
    known_as_of: datetime


class AgentSignal(BaseModel):
    model_name: str
    model_version: str
    agent_name: str
    isin: str
    symbol: str
    sector: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    probability: float = Field(ge=0.0, le=1.0)
    entry: float
    target: float
    stop: float
    atr: float
    opening_gap_pct: float = 0.0
    known_as_of: datetime


class TradeCostBreakdown(BaseModel):
    brokerage_inr: float
    stt_inr: float
    exchange_txn_inr: float
    sebi_inr: float
    stamp_duty_inr: float
    gst_inr: float
    slippage_inr: float
    total_cost_inr: float


class DataQualityReport(BaseModel):
    trade_date: date
    checked_at_ist: datetime
    universe_count: int
    daily_rows: int
    missing_symbols: int
    bad_ohlc_rows: int
    illiquid_dropped: int
    global_cues_ok: bool
    intraday_status: str
    tradeable: bool
    reasons: list[str] = Field(default_factory=list)
