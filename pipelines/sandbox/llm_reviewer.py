"""Clean LLM reviewer interface (no live LLM in this phase; stub returns approve)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from libs.common.schemas import AgentSignal


@dataclass(frozen=True)
class ReviewVerdict:
    decision: str  # "APPROVE" | "VETO"
    reason: str


class LLMReviewer(Protocol):
    def review(self, signal: AgentSignal, context: dict[str, object] | None = None) -> ReviewVerdict:
        """Review a coordinated signal before the deterministic risk engine runs."""


class StubLLMReviewer:
    """Placeholder reviewer returning APPROVE until Claude/ChatGPT is wired in."""

    def review(self, signal: AgentSignal, context: dict[str, object] | None = None) -> ReviewVerdict:
        return ReviewVerdict(decision="APPROVE", reason="llm_reviewer_stub: auto-approve (LLM not enabled)")
