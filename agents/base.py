"""
Base Agent Module for CUBE 10-Agent Trading System

직원 이름·직함·좌석은 `agents/agent_profiles.py`가 정본이다. 여기서는 agent_id로
프로필을 붙여 요약(get_summary)에 실어 보낸다 — 이름을 에이전트별로 하드코딩하지 않는다.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from datetime import datetime

from agents import agent_profiles


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
        p = agent_profiles.profile(agent_id)
        self.person: str = p["name"]
        self.title: str = p["title"]
        self.specialty: str = p["specialty"]
        self.duty: str = p["duty"]
        self.key_metrics = list(p["metrics"])
        self.seat = dict(p["seat"])
        self.icon: str = p["icon"]
        self.accent: str = p["accent"]
        self.display_name: str = agent_profiles.DISPLAY_NAMES.get(agent_id, name)

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
            "person": self.person,
            "title": self.title,
            "display_name": self.display_name,
            "specialty": self.specialty,
            "duty": self.duty,
            "key_metrics": self.key_metrics,
            "seat": self.seat,
            "icon": self.icon,
            "accent": self.accent,
            "code": self.code,
            "role": self.role,
            "description": self.description,
            "status": self.status,
            "last_run": self.last_run or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "confidence_score": round(self.confidence_score, 2),
            "report": self.report
        }
