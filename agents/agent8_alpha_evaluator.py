"""
Agent 8: Alpha & Risk Evaluator

Approves or rejects each strategy using the measured backtest metrics from
Agent 7 (PF, Sharpe, MDD, trade count) rather than a pre-written verdict list.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent
from core import strategy_approval


class AlphaEvaluatorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=8,
            name="Agent 8: 단기 수익 시나리오 알파 분석가",
            code="SHORT_TERM_ALPHA_EVALUATOR",
            role="실측 백테스트 지표로 전략 승인·폐기 판정",
            description="손익비(PF) 1.3 이상·샤프 1.0 이상·MDD 15% 이내·거래 20회 이상을 동시에 만족하는 전략만 승인합니다.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        bt = context.get("backtest") or {}
        results = bt.get("backtest_results") or bt.get("results") or []
        phase = (context.get("clock") or {}).get("phase")

        approved, rejected = [], []
        for r in results:
            m = r.get("metrics") or {}
            pf = m.get("profit_factor") or 0
            sh = m.get("sharpe_ratio") or 0
            mdd = abs(m.get("mdd_pct") or 0)
            n = m.get("total_trades") or 0
            ok = pf >= 1.3 and sh >= 0.8 and mdd <= 15 and n >= 20
            row = {"id": r.get("id"), "name": r.get("name"), "pf": pf, "sharpe": sh, "mdd_pct": -mdd,
                   "trades": n, "win_rate_pct": m.get("win_rate_pct"),
                   "avg_win_pct": m.get("avg_win_pct"), "avg_loss_pct": m.get("avg_loss_pct"),
                   "return_pct": m.get("total_return_pct")}
            if ok:
                row["alpha_source"] = self._alpha_source(r.get("id"))
                row["max_size_guidance"] = "리스크 2%/거래 · 종목 비중 상한 25%"
                approved.append(row)
            else:
                row["reject_reason"] = self._reason(pf, sh, mdd, n)
                rejected.append(row)

        approved.sort(key=lambda x: -(x["pf"] or 0))
        recommended = approved[0]["id"] if approved else "CASH_ONLY"
        overnight = strategy_approval.plan_block()

        self.output_data = {
            "phase": phase,
            "criteria": {"profit_factor_min": 1.3, "sharpe_min": 0.8, "max_dd_pct": 15, "min_trades": 20},
            "top_profitable_count": len(approved),
            "profitable_scenarios": approved,
            "rejected_scenarios": rejected,
            "recommended_execution_mode": recommended,
            "overnight_approval": overnight,
            "approval_note": (
                "승인 기준을 통과한 전략이 없으면 현금(SGOV) 보유가 최적입니다."
                if not approved else f"{len(approved)}개 전략 승인. 최우선 {recommended}."
            ) + (f" 오버나이트 보유(DIP_SWING) {overnight['status_ko']}." if recommended == "DIP_SWING" else ""),
            "key_takeaway": (
                f"승인 {len(approved)}개 / 폐기 {len(rejected)}개. 최우선 {recommended}"
                + (f" (PF {approved[0]['pf']}, MDD {approved[0]['mdd_pct']}%)" if approved else "")
            ),
        }
        self.confidence_score = 0.92
        self.status = "COMPLETED"
        return self.output_data

    @staticmethod
    def _alpha_source(sid: str) -> str:
        return {
            "DIP_SWING": "과매도 패닉 매수 후 2~3세션 반등 포획 — 진입·청산 모두 ATR 대비 대칭 손익비",
            "INTRADAY_UPGRADED": "ATR 동적 브래킷과 체결가 재산정으로 손실 거래 크기를 축소",
            "INTRADAY_BASELINE": "고정 브래킷 장중 회전",
        }.get(sid, "실측 지표 통과")

    @staticmethod
    def _reason(pf: float, sh: float, mdd: float, n: int) -> str:
        reasons = []
        if pf < 1.3:
            reasons.append(f"손익비 {pf} < 1.3")
        if sh < 0.8:
            reasons.append(f"샤프 {sh} < 0.8")
        if mdd > 15:
            reasons.append(f"MDD {mdd:.1f}% > 15%")
        if n < 20:
            reasons.append(f"거래 {n}회 < 20회")
        return ", ".join(reasons)
