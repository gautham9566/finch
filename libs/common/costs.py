"""Config-driven Indian equity intraday cost and slippage model."""

from __future__ import annotations

from typing import Any

from libs.common.config import get_costs_config
from libs.common.schemas import TradeCostBreakdown


def calculate_roundtrip_costs(
    entry_price: float,
    exit_price: float,
    quantity: int,
    direction: str = "LONG",
    gap_pct: float = 0.0,
    cfg: dict[str, Any] | None = None,
) -> TradeCostBreakdown:
    """Compute realistic Indian equity intraday statutory charges + pessimistic slippage."""
    if quantity <= 0 or entry_price <= 0 or exit_price <= 0:
        return TradeCostBreakdown(
            brokerage_inr=0.0,
            stt_inr=0.0,
            exchange_txn_inr=0.0,
            sebi_inr=0.0,
            stamp_duty_inr=0.0,
            gst_inr=0.0,
            slippage_inr=0.0,
            total_cost_inr=0.0,
        )

    c = cfg or get_costs_config()
    buy_val = entry_price * quantity if direction == "LONG" else exit_price * quantity
    sell_val = exit_price * quantity if direction == "LONG" else entry_price * quantity
    turnover = buy_val + sell_val

    brok_buy = min(float(c["brokerage_max_inr"]), buy_val * float(c["brokerage_pct"]))
    brok_sell = min(float(c["brokerage_max_inr"]), sell_val * float(c["brokerage_pct"]))
    brokerage = brok_buy + brok_sell

    stt = sell_val * float(c["stt_sell_pct"])
    exchange_txn = turnover * float(c["exchange_txn_pct"])
    sebi = turnover * float(c["sebi_turnover_pct"])
    stamp_duty = buy_val * float(c["stamp_duty_buy_pct"])
    gst = (brokerage + exchange_txn + sebi) * float(c["gst_pct"])

    base_slip_bps = float(c["slippage_bps"]) / 10000.0
    gap_mult = float(c.get("pessimistic_gap_slippage_mult", 1.5)) if abs(gap_pct) > 0.015 else 1.0
    slippage = turnover * base_slip_bps * gap_mult

    total = brokerage + stt + exchange_txn + sebi + stamp_duty + gst + slippage
    return TradeCostBreakdown(
        brokerage_inr=round(brokerage, 4),
        stt_inr=round(stt, 4),
        exchange_txn_inr=round(exchange_txn, 4),
        sebi_inr=round(sebi, 4),
        stamp_duty_inr=round(stamp_duty, 4),
        gst_inr=round(gst, 4),
        slippage_inr=round(slippage, 4),
        total_cost_inr=round(total, 4),
    )


def apply_pessimistic_fill(
    price: float,
    side: str,
    slippage_bps: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> float:
    """Adjust fill price against the trader by configured slippage bps."""
    c = cfg or get_costs_config()
    bps = float(c["slippage_bps"]) if slippage_bps is None else slippage_bps
    frac = bps / 10000.0
    if side.upper() == "BUY":
        return price * (1.0 + frac)
    return price * (1.0 - frac)
