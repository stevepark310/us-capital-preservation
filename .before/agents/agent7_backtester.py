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
LEI_FILTER_REPORT = os.path.join(BASE_DIR, "data", "lei_filter_backtest.json")


def _load_lei_filter() -> Dict[str, Any]:
    try:
        with open(LEI_FILTER_REPORT, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


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

        # ---- 2026-11-14 확장: 선행지표·장단기금리차 필터의 방어 효과(실측 시뮬)
        filt = _load_lei_filter()
        filter_results = filt.get("results") or {}
        defence = filt.get("defence_effect") or []
        soxl_rows = filter_results.get("SOXL_100") or {}
        base_row = soxl_rows.get("NO_FILTER") or {}
        filt_table = []
        for fname in ("F1_TERM_SPREAD", "F2_SPY_200DMA", "F3_CREDIT_OAS", "F3B_CREDIT_OAS_Z",
                      "F4_COMPOSITE_LEI", "F4_COMPOSITE_LEI_HALFSIZE"):
            m = soxl_rows.get(fname) or {}
            if not m:
                continue
            filt_table.append({
                "filter": fname, "cagr_pct": m.get("cagr_pct"), "mdd_pct": m.get("mdd_pct"),
                "sharpe": m.get("sharpe"), "calmar": m.get("calmar"),
                "time_in_market_pct": m.get("time_in_market_pct"), "switches": m.get("switches"),
                "mdd_delta_pp_vs_unfiltered": round((m.get("mdd_pct") or 0) - (base_row.get("mdd_pct") or 0), 2),
                "cagr_delta_pp_vs_unfiltered": round((m.get("cagr_pct") or 0) - (base_row.get("cagr_pct") or 0), 2),
                "sharpe_delta_vs_unfiltered": round((m.get("sharpe") or 0) - (base_row.get("sharpe") or 0), 2),
            })
        # Empirical verdict driven by the measured signs: filters whose state never
        # triggers are inert and excluded from the judgement.
        verdict = "NO_DATA"
        active = [r for r in filt_table if abs(r["mdd_delta_pp_vs_unfiltered"]) > 0 or abs(r["cagr_delta_pp_vs_unfiltered"]) > 0]
        inert = [r["filter"] for r in filt_table if r not in active]
        mdd_wins = [r for r in active if r["mdd_delta_pp_vs_unfiltered"] > 0]
        sharpe_loss = [r for r in active if r["sharpe_delta_vs_unfiltered"] < 0]
        if active:
            if len(mdd_wins) == len(active) and len(sharpe_loss) == len(active):
                verdict = ("MDD는 모든 발동 필터에서 축소됐지만 Sharpe·CAGR도 함께 악화 → 바이너리 청산보다 "
                           "사이징 축소(포지션 상한)로만 반영해야 함")
            elif not mdd_wins:
                verdict = "이 구간에서 필터의 MDD 방어 효과 없음 → 타이밍 킬스위치로 부적합"
            else:
                verdict = "필터별 결과 엇갈림 — 아래 표의 실측치로 개별 판단"
        if inert:
            verdict += f" (미발동 필터: {', '.join(inert)} — 임계값이 이 구간에서 한 번도 걸리지 않아 무필터와 동일)"
        lei_filter = {
            "is_simulation": filt.get("is_simulation", True),
            "window": filt.get("window"), "friction": filt.get("friction"),
            "filter_definitions": filt.get("filters"),
            "filter_state_days": filt.get("filter_state_days"),
            "unfiltered_baseline": {k: base_row.get(k) for k in ("cagr_pct", "mdd_pct", "sharpe", "calmar")},
            "table": filt_table,
            "portfolio_variants": {p: {f: {k: v.get(k) for k in ("cagr_pct", "mdd_pct", "sharpe", "time_in_market_pct")}
                                      for f, v in rows.items()}
                                  for p, rows in filter_results.items()},
            "defence_effect": defence,
            "verdict": verdict,
            "key_takeaway": ((f"선행지표 필터 백테스트(시뮬, {filt.get('window',{}).get('start')}~{filt.get('window',{}).get('end')}): "
                              f"무필터 SOXL CAGR {base_row.get('cagr_pct')}%/MDD {base_row.get('mdd_pct')}%/Sharpe {base_row.get('sharpe')} 대비 "
                              f"복합필터 MDD {(soxl_rows.get('F4_COMPOSITE_LEI') or {}).get('mdd_pct')}%·Sharpe {(soxl_rows.get('F4_COMPOSITE_LEI') or {}).get('sharpe')}. "
                              f"반감 축소 {((soxl_rows.get('F4_COMPOSITE_LEI_HALFSIZE') or {}).get('cagr_pct'))}%. {verdict}")
                             if filt_table else "LEI 필터 백테스트 미실행 — 다음 파이프라인에서 산출"),
        }

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
            "lei_filter_backtest": lei_filter,
            "benchmarks": rep.get("benchmarks"),
            "key_takeaway": (
                f"현행 장중 회전 {base.get('total_return_pct')}% (PF {base.get('profit_factor')}) → 개선 {up.get('total_return_pct')}% "
                f"(PF {up.get('profit_factor')}), DIP_SWING {swing.get('total_return_pct')}% (PF {swing.get('profit_factor')}, MDD {swing.get('mdd_pct')}%). "
                + (f"최적 전략: {best['name']}." if best else "")
                + (f" 숏 엔진 최적 트리거 VIX {choice.get('vix_trigger')}." if choice else "")
            ),
        }
        self.output_data["key_takeaway"] = (self.output_data["key_takeaway"] + " " + lei_filter["key_takeaway"])[:900]
        self.confidence_score = 0.95 if base else 0.5
        self.status = "COMPLETED"
        return self.output_data
