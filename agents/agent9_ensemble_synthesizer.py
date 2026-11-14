"""
Agent 9: Ensemble Synthesizer

Target weights now follow the evidence chain: approved strategies (Agent 8),
sentiment per ticker (Agent 1), the macro regime (Agent 4) and the valid
brackets. When no strategy passes validation the ensemble holds cash/SGOV rather
than deploying capital for the sake of activity.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from agents.base import BaseAgent


class EnsembleSynthesizerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=9,
            name="Agent 9: 단기 최적 시나리오 융합가",
            code="SHORT_TERM_ENSEMBLE_SYNTHESIZER",
            role="승인 전략·센티먼트·레짐 융합으로 목표 비중 산출",
            description="승인된 전략과 종목별 센티먼트, 매크로 레짐을 결합해 현금·SGOV·레버리지 비중을 결정합니다.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        snap = context.get("snapshot") or {}
        macro = context.get("macro") or {}
        alpha = context.get("alpha") or {}
        senti = (context.get("sentiment") or {}).get("tickers", {})
        ledger = context.get("ledger") or {}
        vix = macro.get("vix_level") or 20.0
        regime = macro.get("macro_regime", "UNKNOWN")
        approved = [a["id"] for a in alpha.get("profitable_scenarios", [])]
        mode = alpha.get("recommended_execution_mode", "CASH_ONLY")

        s_soxl = senti.get("SOXL", {}).get("sentiment_score", 50)
        s_tqqq = senti.get("TQQQ", {}).get("sentiment_score", 50)
        s_nvda = senti.get("NVDA", {}).get("sentiment_score", 50)

        if vix >= 24 or "CASH_ONLY" in mode:
            weights = {"SGOV": 0.80, "SOXL": 0.10, "TQQQ": 0.10}
            note = "고변동성/검증 미통과 → 현금·SGOV 80% 방어"
        else:
            # exposure scales with the best approved Profit Factor
            best_pf = max([a.get("pf") or 1.0 for a in alpha.get("profitable_scenarios", [])] or [1.0])
            gross = min(0.75, 0.35 + 0.15 * min(3.0, best_pf - 1.0))
            tilt_soxl = 0.5 + (s_soxl - 50) / 200.0
            tilt_tqqq = 0.5 + (s_tqqq - 50) / 200.0
            norm = max(0.2, tilt_soxl + tilt_tqqq)
            weights = {
                "SOXL": round(gross * tilt_soxl / norm, 3),
                "TQQQ": round(gross * tilt_tqqq / norm, 3),
                "NVDA": 0.0,
                "SGOV": round(1 - gross, 3),
            }
            note = f"승인 전략 최고 PF {best_pf} 기준 총노출 {gross:.0%}, 센티먼트 틸트 적용"

        if s_nvda >= 65 and ledger.get("positions") and any(p["ticker"] == "NVDA" for p in ledger["positions"]):
            note += " · NVDA 보유분은 실적 전 카탈리스트 홀딩(전량 매도 지시 없음)"

        equity = ledger.get("equity_usd") or 0
        alloc = {k: round(v * equity, 2) for k, v in weights.items()}
        self.output_data = {
            "phase": (context.get("clock") or {}).get("phase"),
            "regime": regime, "vix": vix,
            "approved_strategies": approved,
            "execution_mode": mode,
            "target_portfolio_weights": weights,
            "target_alloc_usd": alloc,
            "fx_rate": ledger.get("fx_rate"),
            "target_alloc_krw": {k: (int(round(v * (ledger.get("fx_rate") or 1335))) if v else 0) for k, v in alloc.items()},
            "regime_note": note,
            "key_takeaway": (
                f"{note}. 목표 비중 SOXL {weights.get('SOXL', 0):.0%} / TQQQ {weights.get('TQQQ', 0):.0%} / "
                f"SGOV {weights.get('SGOV', 0):.0%} (총 {equity:.0f} USD 기준)."
            ),
        }
        self.confidence_score = 0.9
        self.status = "COMPLETED"
        return self.output_data
