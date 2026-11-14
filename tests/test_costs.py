"""Statutory Indian equity intraday costs and pessimistic slippage tests."""

from __future__ import annotations

from libs.common.config import get_costs_config
from libs.common.costs import apply_pessimistic_fill, calculate_roundtrip_costs


def test_zero_quantity_has_zero_cost() -> None:
    c = calculate_roundtrip_costs(100.0, 105.0, 0)
    assert c.total_cost_inr == 0.0
    assert c.brokerage_inr == 0.0


def test_statutory_fees_and_brokerage_cap() -> None:
    cfg = get_costs_config()
    # Large trade value (~25 lakh per leg) should hit INR 20/leg cap => INR 40 total brokerage
    c = calculate_roundtrip_costs(2500.0, 2520.0, 1000, direction="LONG", gap_pct=0.0, cfg=cfg)
    assert c.brokerage_inr == 40.0
    assert c.stt_inr > 0.0
    assert c.exchange_txn_inr > 0.0
    assert c.sebi_inr > 0.0
    assert c.stamp_duty_inr > 0.0
    assert c.gst_inr > 0.0
    assert c.slippage_inr > 0.0
    expected_sum = round(
        c.brokerage_inr + c.stt_inr + c.exchange_txn_inr + c.sebi_inr + c.stamp_duty_inr + c.gst_inr + c.slippage_inr,
        4,
    )
    assert abs(c.total_cost_inr - expected_sum) < 1e-3


def test_wide_gap_applies_pessimistic_slippage_multiplier() -> None:
    cfg = get_costs_config()
    normal = calculate_roundtrip_costs(1000.0, 1010.0, 100, direction="LONG", gap_pct=0.005, cfg=cfg)
    wide_gap = calculate_roundtrip_costs(1000.0, 1010.0, 100, direction="LONG", gap_pct=0.025, cfg=cfg)
    assert wide_gap.slippage_inr > normal.slippage_inr
    assert wide_gap.total_cost_inr > normal.total_cost_inr


def test_pessimistic_fill_adverse_direction() -> None:
    buy_fill = apply_pessimistic_fill(1000.0, "BUY", slippage_bps=10.0)
    sell_fill = apply_pessimistic_fill(1000.0, "SELL", slippage_bps=10.0)
    assert buy_fill > 1000.0
    assert sell_fill < 1000.0
