#!/usr/bin/env python3
"""
CUBE 1-Year Backtest Report Builder

Produces data/1y_backtest_report.json with:
  * baseline vs upgraded intraday rotation (hourly-path engine, same accounting)
  * ablations isolating the fill-anchoring and ATR-scaling changes
  * DIP_SWING portfolio simulation (1y / 2y / split halves)
  * BEAR ALPHA trigger sensitivity sweep + chosen trigger
  * the previously published legacy figure, kept for continuity with a note on
    why it is not reproducible

Everything in the file is SIMULATION and is labelled as such.
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import backtest_lab as lab
from core import swing_engine as sw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY = os.path.join(BASE_DIR, "data", "1y_backtest_report_legacy.json")


def split_halves(daily: dict) -> dict:
    dates = sorted(daily["SOXL"].index)
    mid = dates[len(dates) // 2]
    return {"h1": (str(dates[0].date()), str(mid.date())), "h2": (str(mid.date()), str(dates[-1].date()))}


def main():
    print("[1/4] intraday baseline vs upgraded (hourly path)…")
    rep = lab.ab_compare(period="1y", sweep=True)

    print("[2/4] DIP_SWING portfolio simulation (1y / 2y)…")
    d1 = lab.load_daily("1y")
    d2 = lab.load_daily("2y")
    swing_1y = sw.portfolio_backtest(d1)
    swing_2y = sw.portfolio_backtest(d2)
    swings = {}
    for label, (daily, key) in {"1y": (d1, swing_1y), "2y": (d2, swing_2y)}.items():
        keep = {k: v for k, v in key.items() if k not in ("trades", "equity_curve")}
        keep["monthly_breakdown"] = key.get("monthly_breakdown")
        keep["trade_count"] = len(key.get("trades", []))
        keep["sample_trades"] = key.get("trades", [])[-5:]
        swings[label] = keep
    halves = split_halves(d2)
    swings["split"] = {}
    for k, (s, e) in halves.items():
        r = sw.portfolio_backtest(d2, start=s, end=e)
        swings["split"][k] = {kk: r.get(kk) for kk in ("period", "total_return_pct", "mdd_pct",
                                                       "win_rate_pct", "profit_factor", "total_trades")}
    rep["swing"] = swings

    print("[3/4] legacy comparison…")
    legacy = {}
    if os.path.exists(LEGACY):
        try:
            with open(LEGACY, "r", encoding="utf-8") as f:
                legacy = json.load(f)
        except Exception:
            legacy = {}
    rep["legacy_published"] = {
        "source": "scripts/run_1y_backtest.py (개선 전 스크립트, data/1y_backtest_report_legacy.json)",
        "reported": {k: legacy.get(k) for k in ("total_return_pct", "cagr_pct", "mdd_pct", "win_rate_pct",
                                                "profit_factor", "sharpe_ratio", "total_trades")},
        "why_not_reproducible": [
            "일별 수익률을 [-2.2%, +4.0%]로 강제 클리핑해 손실 꼬리를 잘라냈다.",
            "당일 고가가 TP2에 닿기만 하면 TP1·TP2가 모두 정확한 가격에 체결됐다고 가정했다(완벽 체결 가정).",
            "체결가 대비 슬리피지를 가격에 반영하지 않고, 거래 손익 × 고정 가중치를 단순 합산했다.",
            "본 보고서는 60분봉 경로로 같은 전략을 다시 돌려 손절 우선·슬리피리 반영으로 재측정했다.",
        ],
    }

    print("[4/4] writing report…")
    rep["comparison_table"] = _comparison_table(rep)
    lab.write_report(rep)

    with open(os.path.join(BASE_DIR, "data", "backtest_ab_summary.md"), "w", encoding="utf-8") as f:
        f.write(_markdown(rep))

    for label, r in (("baseline", rep["baseline"]), ("upgraded", rep["upgraded"]),
                     ("dip_swing_1y", swings["1y"]), ("dip_swing_2y", swings["2y"])):
        print(f"{label:13s} ret {r['total_return_pct']:+.2f}%  mdd {r['mdd_pct']:+.2f}%  "
              f"wr {r['win_rate_pct']:.1f}%  pf {r['profit_factor']}  n {r['total_trades']}")
    print("report:", lab.REPORT_PATH)


def _comparison_table(rep: dict) -> list:
    rows = []
    for key, label in (("baseline", "개선 전 (장중 회전·고정 브래킷)"),
                       ("upgraded", "개선 후 (장중 회전·ATR 브래킷)")):
        r = rep.get(key, {})
        rows.append({"label": label, "total_return_pct": r.get("total_return_pct"), "cagr_pct": r.get("cagr_pct"),
                     "win_rate_pct": r.get("win_rate_pct"), "profit_factor": r.get("profit_factor"),
                     "sharpe": r.get("sharpe_ratio"), "mdd_pct": r.get("mdd_pct"),
                     "trades": r.get("total_trades")})
    for k, label in (("1y", "DIP_SWING (1년)"), ("2y", "DIP_SWING (2년)")):
        r = (rep.get("swing") or {}).get(k, {})
        rows.append({"label": label, "total_return_pct": r.get("total_return_pct"),
                     "cagr_pct": r.get("cagr_pct"), "win_rate_pct": r.get("win_rate_pct"),
                     "profit_factor": r.get("profit_factor"), "sharpe": r.get("sharpe_ratio"),
                     "mdd_pct": r.get("mdd_pct"), "trades": r.get("total_trades")})
    return rows


def _markdown(rep: dict) -> str:
    lines = ["# CUBE 매수·매도 알고리즘 개선 전/후 백테스트 (시뮬레이션)", ""]
    lines.append(f"- 생성: {rep.get('generated_at_kst')} KST")
    lines.append(f"- 데이터: {(rep.get('data') or {}).get('source')}")
    lines.append(f"- 마찰: {(rep.get('data') or {}).get('friction')}")
    lines.append(f"- 체결 모델: {(rep.get('data') or {}).get('fill_model')}")
    lines.append("")
    lines.append("> 실전 원장(daily_trading_calendar.json / live_portfolio.json)과 분리된 **시뮬레이션** 수치입니다.")
    lines.append("")
    lines.append("| 전략 | 총수익률 | CAGR | 승률 | PF | Sharpe | MDD | 거래수 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rep.get("comparison_table", []):
        lines.append(f"| {r['label']} | {r.get('total_return_pct')}% | {r.get('cagr_pct')}% | "
                     f"{r.get('win_rate_pct')}% | {r.get('profit_factor')} | {r.get('sharpe')} | "
                     f"{r.get('mdd_pct')}% | {r.get('trades')} |")
    lines.append("")
    ab = rep.get("ablations", {})
    lines.append("## 단일 변경 요인 분리(ablation)")
    for k, label in (("fill_anchor_removed", "체결가 재산정 제거"),
                     ("atr_scaling_removed", "ATR 스케일링 제거")):
        a = ab.get(k, {})
        lines.append(f"- {label}: {a.get('total_return_pct')}% (PF {a.get('profit_factor')}, MDD {a.get('mdd_pct')}%)")
    lines.append("")
    choice = rep.get("bear_engine_choice")
    if choice:
        d = choice.get("detail", {})
        lines.append("## BEAR ALPHA(숏) 엔진 트리거 최적화")
        lines.append(f"- 선택: VIX ≥ {choice.get('vix_trigger')} + 확인 {choice.get('confirmations_required')}개 "
                     f"(총수익 {d.get('total_return_pct')}%, MDD {d.get('mdd_pct')}%, 숏 {d.get('bear_trades')}회, "
                     f"숏 승률 {d.get('bear_win_rate_pct')}%, 숏 기여 {d.get('bear_pnl_contrib_pct')}%)")
        lines.append("")
        lines.append("| VIX 트리거 | 확인 | 총수익 | MDD | 숏 거래 | 숏 승률 | 숏 기여 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rep.get("bear_engine_sweep", []):
            lines.append(f"| {r['vix_trigger']} | {r['confirmations_required']} | {r['total_return_pct']}% | "
                         f"{r['mdd_pct']}% | {r['bear_trades']} | {r['bear_win_rate_pct']}% | {r['bear_pnl_contrib_pct']}% |")
    lines.append("")
    lp = rep.get("legacy_published", {})
    if lp.get("reported"):
        lines.append("## 기존에 발표된 수치와의 차이")
        lines.append(f"- 기존 발표: {lp['reported'].get('total_return_pct')}% / PF {lp['reported'].get('profit_factor')} / "
                     f"MDD {lp['reported'].get('mdd_pct')}%")
        for why in lp.get("why_not_reproducible", []):
            lines.append(f"  - {why}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
