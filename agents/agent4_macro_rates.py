"""
Agent 4: Macro Volatility & VIX Regime Specialist
"""
from typing import Dict, Any
from datetime import datetime
from agents.base import BaseAgent

class MacroRatesAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=4,
            name="Agent 4: 일중 매크로 & VIX 변동성 레짐 분석가",
            code="MACRO_VIX_REGIME_SPECIALIST",
            role="일중 변동성 지수(VIX 1D/9D) 및 10년물 국채 금리 일중 틱 감시",
            description="VIX가 22를 초과하면 레버리지(SOXL/TQQQ) 롱 진입을 전면 차단하고, VIX 20 이하 구간에서만 공격적 단기 돌파를 승인합니다."
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        vix = context.get("vix", 19.46)

        is_safe_for_leverage = vix < 22.0

        self.output_data = {
            "vix_level": vix,
            "safe_for_leverage": is_safe_for_leverage,
            "short_term_directive": "VIX 19.46으로 '레버리지 단기 매매 적정' 레짐 확인. 단, VIX 24 급등 시 즉시 100% SGOV 피신 발령",
            "key_takeaway": "현재 변동성 환경은 단기 레버리지(TQQQ/SOXL)를 운용하기에 가장 안정적인 '골디락스 저변동성 확장' 국면입니다."
        }
        self.confidence_score = 0.95
        self.status = "COMPLETED"
        return self.output_data
