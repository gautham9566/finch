"""Plain-code deterministic RiskEngine with final veto over every candidate trade."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from typing import Any

from libs.common.config import get_risk_config
from libs.common.db import get_conn
from libs.common.schemas import AgentSignal


@dataclass
class RiskDecision:
    approved: bool
    quantity: int
    reason: str


@dataclass
class DailyRiskState:
    trades_taken: int = 0
    realized_pnl_inr: float = 0.0
    symbol_exposure_inr: dict[str, float] = field(default_factory=dict)
    sector_exposure_inr: dict[str, float] = field(default_factory=dict)


def _is_db_kill_switch_on() -> bool:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM system_state WHERE key = 'kill_switch'")
            row = cur.fetchone()
            return bool(row and str(row["value"]).lower() in ("true", "1", "on"))
    except Exception:  # noqa: BLE001
        return False


class RiskEngine:
    """Hardcoded risk controls; never delegated to an ML model."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self.cfg = cfg or get_risk_config()

    def evaluate(
        self,
        signal: AgentSignal,
        state: DailyRiskState,
        decision_time_ist: dtime,
        day_tradeable: bool = True,
        kill_switch_override: bool | None = None,
    ) -> RiskDecision:
        if not day_tradeable:
            return RiskDecision(False, 0, "VETO_DATA_QUALITY: day marked untradeable")

        kill_on = (
            kill_switch_override
            if kill_switch_override is not None
            else (bool(self.cfg.get("kill_switch", False)) or _is_db_kill_switch_on())
        )
        if kill_on:
            return RiskDecision(False, 0, "VETO_KILL_SWITCH: global kill switch active")

        if signal.direction == "FLAT":
            return RiskDecision(False, 0, "SKIP_FLAT: model did not trigger directional pick")

        cutoff_h, cutoff_m = (int(x) for x in str(self.cfg.get("entry_cutoff_ist", "11:30")).split(":"))
        if decision_time_ist > dtime(cutoff_h, cutoff_m):
            return RiskDecision(False, 0, f"VETO_CUTOFF_TIME: {decision_time_ist} > {cutoff_h:02d}:{cutoff_m:02d}")

        min_prob = float(self.cfg.get("min_probability", 0.52))
        if signal.probability < min_prob:
            return RiskDecision(False, 0, f"VETO_LOW_PROB: {signal.probability:.3f} < {min_prob:.3f}")

        max_gap = float(self.cfg.get("max_opening_gap_pct", 0.035))
        if abs(signal.opening_gap_pct) > max_gap:
            return RiskDecision(False, 0, f"VETO_LARGE_GAP: {signal.opening_gap_pct:.3%} > {max_gap:.3%}")

        capital = float(self.cfg.get("capital_inr", 1_000_000.0))
        max_daily_loss = capital * float(self.cfg.get("max_daily_loss_pct", 0.02))
        if state.realized_pnl_inr <= -max_daily_loss:
            return RiskDecision(
                False, 0, f"VETO_DAILY_LOSS_LIMIT: realized {state.realized_pnl_inr:.2f} <= -{max_daily_loss:.2f}"
            )

        max_trades = int(self.cfg.get("max_trades_per_day", 6))
        if state.trades_taken >= max_trades:
            return RiskDecision(False, 0, f"VETO_MAX_TRADES: {state.trades_taken} >= {max_trades}")

        risk_per_share = abs(signal.entry - signal.stop)
        if risk_per_share <= 0 or signal.entry <= 0:
            return RiskDecision(False, 0, "VETO_INVALID_PRICE_OR_STOP")

        risk_budget = capital * float(self.cfg.get("risk_per_trade_pct", 0.005))
        raw_qty = int(risk_budget / risk_per_share)

        max_pos_inr = capital * float(self.cfg.get("max_position_pct", 0.10))
        cur_sym_inr = state.symbol_exposure_inr.get(signal.symbol, 0.0)
        avail_sym_inr = max(0.0, max_pos_inr - cur_sym_inr)

        max_sec_inr = capital * float(self.cfg.get("max_sector_pct", 0.25))
        cur_sec_inr = state.sector_exposure_inr.get(signal.sector, 0.0)
        avail_sec_inr = max(0.0, max_sec_inr - cur_sec_inr)

        max_qty_by_cap = int(min(avail_sym_inr, avail_sec_inr) / signal.entry)
        qty = min(raw_qty, max_qty_by_cap)
        if qty <= 0:
            return RiskDecision(
                False,
                0,
                (
                    f"VETO_EXPOSURE_CAP: symbol={cur_sym_inr:.0f}/{max_pos_inr:.0f}, "
                    f"sector={cur_sec_inr:.0f}/{max_sec_inr:.0f}"
                ),
            )

        notional = qty * signal.entry
        state.trades_taken += 1
        state.symbol_exposure_inr[signal.symbol] = cur_sym_inr + notional
        state.sector_exposure_inr[signal.sector] = cur_sec_inr + notional
        return RiskDecision(True, qty, f"APPROVED: qty={qty}, notional={notional:.2f}")
