"""Agent B: Mean-reversion signal generator."""

from pipelines.training.agents.base import BaseAgent


class MeanReversionAgent(BaseAgent):
    agent_name = "agent_b_mean_reversion"
