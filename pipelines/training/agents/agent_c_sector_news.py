"""Agent C: Sector relative strength & news catalyst signal generator."""

from pipelines.training.agents.base import BaseAgent


class SectorNewsAgent(BaseAgent):
    agent_name = "agent_c_sector_news"
