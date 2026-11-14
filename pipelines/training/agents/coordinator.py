"""Rule-based coordinator: agreement boosts confidence, conflict skips."""

from __future__ import annotations

from dataclasses import dataclass

from libs.common.schemas import AgentSignal


@dataclass
class CoordinatedPick:
    signal: AgentSignal
    decision: str  # "APPROVE_AGREEMENT" | "APPROVE_SINGLE" | "CONFLICT_SKIP" | "BELOW_THRESHOLD"
    reason: str
    adjusted_probability: float


def coordinate_signals(signals: list[AgentSignal], min_prob: float = 0.52) -> list[CoordinatedPick]:
    """Group candidate signals by ISIN across agents/models and resolve agreement vs conflict."""
    by_isin: dict[str, list[AgentSignal]] = {}
    for sig in signals:
        by_isin.setdefault(sig.isin, []).append(sig)

    results: list[CoordinatedPick] = []
    for _isin, grp in by_isin.items():
        active = [s for s in grp if s.direction != "FLAT"]
        if not active:
            top = max(grp, key=lambda s: s.probability)
            results.append(
                CoordinatedPick(
                    signal=top,
                    decision="BELOW_THRESHOLD",
                    reason=f"No active directional signal (max prob={top.probability:.3f})",
                    adjusted_probability=top.probability,
                )
            )
            continue

        dirs = {s.direction for s in active}
        if len(dirs) > 1:
            top = max(active, key=lambda s: s.probability)
            results.append(
                CoordinatedPick(
                    signal=top,
                    decision="CONFLICT_SKIP",
                    reason=f"Directional conflict across agents ({sorted(dirs)})",
                    adjusted_probability=top.probability,
                )
            )
            continue

        # Agreement across multiple models/agents boosts confidence bounded to [0, 0.95]
        top = max(active, key=lambda s: s.probability)
        avg_prob = sum(s.probability for s in active) / len(active)
        boost = 0.03 * (len(active) - 1)
        adj_prob = round(min(0.95, max(top.probability, avg_prob) + boost), 5)

        if adj_prob < min_prob:
            results.append(
                CoordinatedPick(
                    signal=top,
                    decision="BELOW_THRESHOLD",
                    reason=f"Adjusted probability {adj_prob:.3f} < {min_prob:.3f}",
                    adjusted_probability=adj_prob,
                )
            )
        else:
            decision = "APPROVE_AGREEMENT" if len(active) > 1 else "APPROVE_SINGLE"
            reason = (
                f"{len(active)} models agree ({top.direction}); boosted {top.probability:.3f}->{adj_prob:.3f}"
                if len(active) > 1
                else f"Single active signal ({top.model_name}, prob={adj_prob:.3f})"
            )
            results.append(
                CoordinatedPick(
                    signal=top.model_copy(update={"probability": adj_prob}),
                    decision=decision,
                    reason=reason,
                    adjusted_probability=adj_prob,
                )
            )
    return sorted(results, key=lambda r: r.adjusted_probability, reverse=True)


def get_agent_for_name(agent_key: str):
    from pipelines.training.agents.agent_a_momentum import MomentumAgent
    from pipelines.training.agents.agent_b_mean_reversion import MeanReversionAgent
    from pipelines.training.agents.agent_c_sector_news import SectorNewsAgent

    mapping = {
        "momentum": MomentumAgent(),
        "mean_reversion": MeanReversionAgent(),
        "sector_news": SectorNewsAgent(),
    }
    return mapping.get(agent_key, MomentumAgent())
