"""
Base Agent Module for CUBE 10-Agent Trading System
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from datetime import datetime


class BaseAgent(ABC):
    def __init__(self, agent_id: int, name: str, code: str, role: str, description: str):
        self.agent_id = agent_id
        self.name = name
        self.code = code
        self.role = role
        self.description = description
        self.status = "INITIALIZED"
        self.last_run: str = ""
        self.confidence_score: float = 0.0
        self.report: Dict[str, Any] = {}

    @abstractmethod
    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the agent's core specialized analysis and return report dictionary.
        """
        pass

    def get_summary(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "code": self.code,
            "role": self.role,
            "description": self.description,
            "status": self.status,
            "last_run": self.last_run or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "confidence_score": round(self.confidence_score, 2),
            "report": self.report
        }
