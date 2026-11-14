"""Paper broker simulator matching the interface a real broker adapter would implement later."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from libs.common.config import get_costs_config
from libs.common.costs import apply_pessimistic_fill, calculate_roundtrip_costs
from libs.common.schemas import AgentSignal, TradeCostBreakdown


@dataclass(frozen=True)
class SimulatedFill:
    requested_qty: int
    filled_qty: int
    fill_price: float
    exit_price: float
    exit_reason: str  # "TARGET" | "STOP" | "EOD_SQUARE_OFF" | "UNFILLED"
    costs: TradeCostBreakdown
    gross_pnl_inr: float
    net_pnl_inr: float


class BrokerAdapter(ABC):
    """Interface for paper execution now (or live broker adapter in future phases)."""

    @abstractmethod
    def simulate_intraday_trade(
        self,
        signal: AgentSignal,
        quantity: int,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_volume: int,
    ) -> SimulatedFill:
        """Execute and resolve an intraday bracket order against market prices."""


class PaperBroker(BrokerAdapter):
    """Deterministic paper broker with pessimistic slippage, partial fills, and full statutory costs."""

    def __init__(self, costs_cfg: dict[str, Any] | None = None, max_volume_participation: float = 0.02) -> None:
        self.costs_cfg = costs_cfg or get_costs_config()
        self.max_vol_part = max_volume_participation

    def simulate_intraday_trade(
        self,
        signal: AgentSignal,
        quantity: int,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_volume: int,
    ) -> SimulatedFill:
        if quantity <= 0 or bar_open <= 0:
            zero_cost = calculate_roundtrip_costs(0.0, 0.0, 0, cfg=self.costs_cfg)
            return SimulatedFill(quantity, 0, 0.0, 0.0, "UNFILLED", zero_cost, 0.0, 0.0)

        # Partial fill constraint: do not exceed configured participation in day's liquidity
        max_liq_qty = max(1, int(max(bar_volume, 1000) * self.max_vol_part))
        filled_qty = min(quantity, max_liq_qty)

        # Pessimistic entry fill with slippage
        fill_price = apply_pessimistic_fill(
            bar_open, "BUY" if signal.direction == "LONG" else "SELL", cfg=self.costs_cfg
        )

        # Evaluate target / stop / forced 15:15 square-off; pessimistic if both hit on same daily bar
        if signal.direction == "LONG":
            hit_stop = bar_low <= signal.stop
            hit_target = bar_high >= signal.target
            if hit_stop:
                raw_exit = min(bar_open, signal.stop) if bar_open < signal.stop else signal.stop
                exit_reason = "STOP"
            elif hit_target:
                raw_exit = signal.target
                exit_reason = "TARGET"
            else:
                raw_exit = bar_close
                exit_reason = "EOD_SQUARE_OFF"
            exit_price = apply_pessimistic_fill(raw_exit, "SELL", cfg=self.costs_cfg)
            gross_pnl = (raw_exit - bar_open) * filled_qty
        else:
            hit_stop = bar_high >= signal.stop
            hit_target = bar_low <= signal.target
            if hit_stop:
                raw_exit = max(bar_open, signal.stop) if bar_open > signal.stop else signal.stop
                exit_reason = "STOP"
            elif hit_target:
                raw_exit = signal.target
                exit_reason = "TARGET"
            else:
                raw_exit = bar_close
                exit_reason = "EOD_SQUARE_OFF"
            exit_price = apply_pessimistic_fill(raw_exit, "BUY", cfg=self.costs_cfg)
            gross_pnl = (bar_open - raw_exit) * filled_qty

        costs = calculate_roundtrip_costs(
            entry_price=fill_price,
            exit_price=exit_price,
            quantity=filled_qty,
            direction=signal.direction,
            gap_pct=signal.opening_gap_pct,
            cfg=self.costs_cfg,
        )
        # Note: fill_price and exit_price already embed slippage relative to bar_open/raw_exit;
        # avoid double-counting slippage in net_pnl by deducting statutory fees + exact fill slippage once.
        statutory_only = costs.total_cost_inr - costs.slippage_inr
        slip_loss = abs(fill_price - bar_open) * filled_qty + abs(exit_price - raw_exit) * filled_qty
        net_pnl = gross_pnl - statutory_only - slip_loss

        return SimulatedFill(
            requested_qty=quantity,
            filled_qty=filled_qty,
            fill_price=round(fill_price, 4),
            exit_price=round(exit_price, 4),
            exit_reason=exit_reason,
            costs=costs.model_copy(
                update={
                    "slippage_inr": round(slip_loss, 4),
                    "total_cost_inr": round(statutory_only + slip_loss, 4),
                }
            ),
            gross_pnl_inr=round(gross_pnl, 2),
            net_pnl_inr=round(net_pnl, 2),
        )
