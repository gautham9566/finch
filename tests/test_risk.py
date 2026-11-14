"""Plain-code RiskEngine veto tests and LLM reviewer stub contract tests."""

from __future__ import annotations

from datetime import time as dtime

from pipelines.sandbox.llm_reviewer import StubLLMReviewer
from pipelines.sandbox.risk import DailyRiskState, RiskEngine


def test_llm_reviewer_stub_returns_approve(sample_signal) -> None:
    verdict = StubLLMReviewer().review(sample_signal, {"test": True})
    assert verdict.decision == "APPROVE"
    assert "stub" in verdict.reason.lower()


def test_risk_engine_approves_valid_signal_and_sizes_position(sample_signal) -> None:
    engine = RiskEngine()
    state = DailyRiskState()
    dec = engine.evaluate(sample_signal, state, dtime(9, 20), day_tradeable=True, kill_switch_override=False)
    assert dec.approved is True
    assert dec.quantity > 0
    assert dec.quantity * sample_signal.entry <= 100_000.0 + sample_signal.entry


def test_risk_engine_vetoes_untradeable_day_and_kill_switch(sample_signal) -> None:
    engine = RiskEngine()
    state = DailyRiskState()
    d_untrade = engine.evaluate(sample_signal, state, dtime(9, 20), day_tradeable=False, kill_switch_override=False)
    assert d_untrade.approved is False
    assert "VETO_DATA_QUALITY" in d_untrade.reason

    d_kill = engine.evaluate(sample_signal, state, dtime(9, 20), day_tradeable=True, kill_switch_override=True)
    assert d_kill.approved is False
    assert "VETO_KILL_SWITCH" in d_kill.reason


def test_risk_engine_vetoes_cutoff_gap_probability_loss_and_limits(sample_signal) -> None:
    engine = RiskEngine()

    late = engine.evaluate(
        sample_signal, DailyRiskState(), dtime(12, 0), day_tradeable=True, kill_switch_override=False
    )
    assert not late.approved and "VETO_CUTOFF_TIME" in late.reason

    low_prob = sample_signal.model_copy(update={"probability": 0.48})
    dec_prob = engine.evaluate(low_prob, DailyRiskState(), dtime(9, 20), day_tradeable=True, kill_switch_override=False)
    assert not dec_prob.approved and "VETO_LOW_PROB" in dec_prob.reason

    big_gap = sample_signal.model_copy(update={"opening_gap_pct": 0.06})
    dec_gap = engine.evaluate(big_gap, DailyRiskState(), dtime(9, 20), day_tradeable=True, kill_switch_override=False)
    assert not dec_gap.approved and "VETO_LARGE_GAP" in dec_gap.reason

    loss_state = DailyRiskState(realized_pnl_inr=-25_000.0)
    dec_loss = engine.evaluate(sample_signal, loss_state, dtime(9, 20), day_tradeable=True, kill_switch_override=False)
    assert not dec_loss.approved and "VETO_DAILY_LOSS_LIMIT" in dec_loss.reason

    max_trades_state = DailyRiskState(trades_taken=6)
    dec_max = engine.evaluate(
        sample_signal, max_trades_state, dtime(9, 20), day_tradeable=True, kill_switch_override=False
    )
    assert not dec_max.approved and "VETO_MAX_TRADES" in dec_max.reason

    sec_state = DailyRiskState(sector_exposure_inr={"Energy": 250_000.0})
    dec_sec = engine.evaluate(sample_signal, sec_state, dtime(9, 20), day_tradeable=True, kill_switch_override=False)
    assert not dec_sec.approved
