"""
Agent 7: Backtest Verification Engineer (reads measured results, not invented ones)

The previous version returned a literal list of results ("+142.8%, PF 2.14 ...").
This version reads data/1y_backtest_report.json produced by core/backtest_lab
(hourly-path engine, 0.24% friction, stop-first sequencing) and reports exactly
what the simulation measured — including the fact that the intraday rotation is
currently a losing strategy and the dip-swing variant is the one with an edge.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from agents.base import BaseAgent

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(BASE_DIR, "data", "1y_backtest_report.json")


def _load() -> Dict[str, Any]:
    try:
        with open(REPORT, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class BacktesterAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=7,
            name="Agent 7: 단기 시나리오 백테스트 검증 엔지니어",
            code="SHORT_TERM_BACKTESTER_ENGINEER",
            role="시간봉 경로 기반 개선 전/후 성과 검증",
            description="core/backtest_lab의 시간봉(60m) 백테스트 결과를 읽어 개선 전·후, DIP_SWING, 하락장 숏 엔진 민감도를 실측 수치로 보고합니다. 왕복 0.24% 마찰 반영.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        rep = _load()
        age_h = None
        if rep.get("generated_at_kst"):
            try:
                t = datetime.strptime(rep["generated_at_kst"], "%Y-%m-%d %H:%M:%S")
                age_h = round(((datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)) - t).total_seconds() / 3600, 1)
            except Exception:
                age_h = None

        base = rep.get("baseline", {})
        up = rep.get("upgraded", {})
        swing = (rep.get("swing") or {}).get("1y", {})
        sweep = rep.get("bear_engine_sweep") or []
        choice = rep.get("bear_engine_choice") or {}

        def brief(r):
            if not r:
                return {}
            return {k: r.get(k) for k in ("total_return_pct", "cagr_pct", "mdd_pct", "win_rate_pct",
                                          "profit_factor", "sharpe_ratio", "total_trades", "avg_win_pct", "avg_loss_pct")}

        results = [
            {"id": "INTRADAY_BASELINE", "name": "현행 장중 회전 (고정 2.0/3.5/1.5%)", "metrics": brief(base),
             "status": "REJECTED (마찰 후 음의 기대값)" if (base.get("total_return_pct") or 0) <= 0 else "PROFITABLE"},
            {"id": "INTRADAY_UPGRADED", "name": "개선 장중 회전 (ATR 브래킷·체결가 재산정·러너 트레일)", "metrics": brief(up),
             "status": "IMPROVED_BUT_NEGATIVE" if (up.get("total_return_pct") or 0) <= 0 else "PROFITABLE"},
            {"id": "DIP_SWING", "name": "과매도 패닉 매수·2~3세션 보유 (DIP_SWING)", "metrics": brief(swing),
             "status": "VALIDATED_PROFITABLE" if (swing.get("profit_factor") or 0) > 1.3 else "NOT_VALIDATED"},
        ]
        for s in results:
            if s["metrics"].get("win_rate_pct") is None:
                s["status"] = "NO_DATA"

        best = max([r for r in results if r["metrics"].get("profit_factor")], key=lambda r: r["metrics"]["profit_factor"], default=None)

        self.output_data = {
            "phase": (context.get("clock") or {}).get("phase"),
            "report_path": "data/1y_backtest_report.json",
            "report_generated_at_kst": rep.get("generated_at_kst"),
            "report_age_hours": age_h,
            "is_simulation": True,
            "friction": "왕복 0.24% (NH 0.09% + 슬리피지 0.15%)",
            "fill_model": (rep.get("data") or {}).get("fill_model"),
            "backtest_results": results,
            "ablations": rep.get("ablations"),
            "delta": rep.get("delta"),
            "bear_engine_sweep": sweep,
            "bear_engine_choice": choice,
            "benchmarks": rep.get("benchmarks"),
            "key_takeaway": (
                f"현행 장중 회전 {base.get('total_return_pct')}% (PF {base.get('profit_factor')}) → 개선 {up.get('total_return_pct')}% "
                f"(PF {up.get('profit_factor')}), DIP_SWING {swing.get('total_return_pct')}% (PF {swing.get('profit_factor')}, MDD {swing.get('mdd_pct')}%). "
                + (f"최적 전략: {best['name']}." if best else "")
                + (f" 숏 엔진 최적 트리거 VIX {choice.get('vix_trigger')}." if choice else "")
            ),
        }
        self.confidence_score = 0.95 if base else 0.5
        self.status = "COMPLETED"
        return self.output_data
