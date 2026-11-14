"""PaperBroker execution simulator tests (pessimistic target/stop, liquidity cap, costs)."""

from __future__ import annotations

from pipelines.sandbox.broker import PaperBroker


def test_pessimistic_same_bar_hits_stop_not_target(sample_signal) -> None:
    broker = PaperBroker(max_volume_participation=0.02)
    # Bar sweeps both stop (2470) and target (2545) -> must exit at STOP pessimistically
    fill = broker.simulate_intraday_trade(
        signal=sample_signal,
        quantity=40,
        bar_open=2500.0,
        bar_high=2560.0,
        bar_low=2455.0,
        bar_close=2530.0,
        bar_volume=100_000,
    )
    assert fill.filled_qty == 40
    assert fill.exit_reason == "STOP"
    assert fill.net_pnl_inr < 0.0
    assert fill.costs.total_cost_inr > 0.0


def test_target_hit_when_stop_untouched(sample_signal) -> None:
    broker = PaperBroker(max_volume_participation=0.02)
    fill = broker.simulate_intraday_trade(
        signal=sample_signal,
        quantity=40,
        bar_open=2500.0,
        bar_high=2560.0,
        bar_low=2490.0,
        bar_close=2540.0,
        bar_volume=100_000,
    )
    assert fill.exit_reason == "TARGET"
    assert fill.gross_pnl_inr > 0.0
    assert fill.net_pnl_inr < fill.gross_pnl_inr


def test_eod_square_off_and_volume_participation_cap(sample_signal) -> None:
    broker = PaperBroker(max_volume_participation=0.02)
    # Volume 2000 -> 2% cap = 40 shares even if requested 500
    fill = broker.simulate_intraday_trade(
        signal=sample_signal,
        quantity=500,
        bar_open=2500.0,
        bar_high=2525.0,
        bar_low=2485.0,
        bar_close=2515.0,
        bar_volume=2_000,
    )
    assert fill.filled_qty == 40
    assert fill.exit_reason == "EOD_SQUARE_OFF"
