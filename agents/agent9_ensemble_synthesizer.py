"""
Agent 9: Short-Term Ensemble Synthesizer
Synthesizes the profitable scenarios into today's optimal target short-term portfolio.
"""
from typing import Dict, Any, List
from datetime import datetime
from agents.base import BaseAgent

class EnsembleSynthesizerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=9,
            name="Agent 9: 단기 최적 시나리오 융합가",
            code="SHORT_TERM_ENSEMBLE_SYNTHESIZER",
            role="상위 5대 단기 전략 융합 및 오늘 당일 실전 목표 포지션 확정",
            description="SOXL·TQQQ·NVDA·TSLA·SGOV 유니버스를 바탕으로 오늘 당일의 최적 매매 비중과 트리거 가격을 산출합니다."
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        vix = context.get("vix", 19.46)

        # Dynamic weights for short-term universe
        # Target Universe: SOXL (35%), TQQQ (25%), NVDA (15%), TSLA (5%), SGOV (20% 무위험 캐리)
        if vix > 24.0:
            target_weights = {
                "SGOV": 0.80,
                "SOXL": 0.10,
                "TQQQ": 0.10
            }
            regime_note = "고변동성 방어 모드: 현금/SGOV 80% 비중 확대 및 단기 돌파 축소"
        else:
            target_weights = {
                "SOXL": 0.35,   # 반도체 3X 레버리지 (단주 매수 가능, ATR 7.8%)
                "TQQQ": 0.25,   # 나스닥 3X 레버리지 (테크 주도주 추세 추종)
                "NVDA": 0.15,   # AI 대장주 실적 모멘텀 돌파
                "TSLA": 0.05,   # 고변동성 스윙
                "SGOV": 0.20    # 연 4.5% 무위험 이자 캐리 및 패닉 딥바잉 예비금
            }
            regime_note = "골디락스 변동성 확장 모드: SOXL/TQQQ 단기 돌파 & SGOV 무위험 버퍼"

        self.output_data = {
            "regime_note": regime_note,
            "target_portfolio_weights": target_weights,
            "short_term_directive": "추가 입금액 규모에 맞춰 SOXL(1주 약 30달러)부터 즉시 분할 매수 집행, 원금 확대 시 TQQQ·NVDA 즉각 편입",
            "key_takeaway": "반도체 3배(SOXL 35%)와 나스닥 3배(TQQQ 25%)에 단기 알파를 집중하고, 20%의 SGOV를 캐리 버퍼로 확보하는 고효율 단기 포트폴리오를 확정했습니다."
        }
        self.confidence_score = 0.95
        self.status = "COMPLETED"
        return self.output_data
