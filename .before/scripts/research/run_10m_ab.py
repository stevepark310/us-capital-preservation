#!/usr/bin/env python3
"""(C) 신규 롱·숏 변곡점 알고리즘 후보 5종 — 10분 단위 A/B 백테스트 (5거래일 vs 60거래일).

실행: python3 scripts/research/run_10m_ab.py
출력: data/research_10m_ab_20261118.json
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import i10_lib as L                     # noqa: E402
import i10_strategies as S              # noqa: E402
from core import execution_policy       # noqa: E402

DATA = os.path.join(L.BASE_DIR, "data")
OUT = os.path.join(DATA, "research_10m_ab_20261118.json")
PAIRS = [("SOXL", "SOXS"), ("TQQQ", "SQQQ")]
BUDGET = 150.0
DAYS5 = L.SESSION_DAYS

CONFIGS: Dict[str, Dict[str, Any]] = {
    "C1": {"z": 2.0, "stop_atr": 1.0, "trail_atr": None, "max_hold": 6, "exit_mode": "vwap"},
    "C2": {"adx_min": 25.0, "rvol_min": 1.2, "stop_atr": 2.0, "trail_atr": 2.0, "max_hold": None},
    "C3": {"rsi_lo": 5.0, "rsi_hi": 95.0, "stop_atr": 1.5, "trail_atr": None, "max_hold": 12,
           "exit_mode": "rsi50"},
    "C4": {"stop_atr": 1.2, "trail_atr": 1.2, "max_hold": None, "exit_mode": "don6"},
}
C5 = {"entry_hm": "10:00", "exit_hm": "15:40", "stop_atr": 3.0}


def merge(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    return L.trade_stats(trades, BUDGET)


def run_intraday(name: str, days: List[str], panel: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    per_pair = {}
    all_trades: List[Dict[str, Any]] = []
    for under, inv in PAIRS:
        r = S.simulate(name, panel[under], panel[inv], days, CONFIGS[name], under, inv, BUDGET)
        per_pair[f"{under}/{inv}"] = r["stats"]
        all_trades += r["trades"]
    st = merge(all_trades)
    return {"params": CONFIGS[name], "stats": st, "per_pair": per_pair,
            "trades": sorted(all_trades, key=lambda t: (t["date"], t["entry_ts"]))}


def run_gap(days: List[str], panel: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    per_pair, all_trades = {}, []
    for under, inv in PAIRS:
        r = S.simulate_gap_day(panel[under], panel[inv], days, stop_atr=C5["stop_atr"],
                               entry_hm=C5["entry_hm"], exit_hm=C5["exit_hm"], budget=BUDGET,
                               under_sym=under, inv_sym=inv)
        per_pair[f"{under}/{inv}"] = r["stats"]
        all_trades += r["trades"]
    st = merge(all_trades)
    return {"params": C5, "stats": st, "per_pair": per_pair,
            "trades": sorted(all_trades, key=lambda t: (t["date"], t["entry_ts"]))}


def oracle(days: List[str], panel: Dict[str, pd.DataFrame], thr: float = 0.8) -> Dict[str, Any]:
    """사후 완전예지 상한 — 10분 지그재그 모든 leg를 정확히 잡는 경우($150 기준)."""
    tot, legs, per_day = 0.0, 0, {}
    for d in days:
        dsum = 0.0
        for under, inv in PAIRS:
            g = panel[under][panel[under]["session"] == d]
            if len(g) < 3:
                continue
            c = list(map(float, g["Close"].values))
            idx = list(g.index)
            pts, trend, ext, ext_i = [], 0, c[0], 0
            for i in range(1, len(c)):
                if trend >= 0:
                    if c[i] > ext:
                        ext, ext_i = c[i], i
                    elif (ext - c[i]) / ext * 100 >= thr:
                        pts.append(("H", ext_i))
                        trend, ext, ext_i = -1, c[i], i
                if trend <= 0:
                    if c[i] < ext:
                        ext, ext_i = c[i], i
                    elif (c[i] - ext) / ext * 100 >= thr:
                        pts.append(("L", ext_i))
                        trend, ext, ext_i = 1, c[i], i
            ii = panel[inv][panel[inv]["session"] == d]
            mp = {str(ts): float(px) for ts, px in ii["Close"].items()}
            for a, b in zip(pts, pts[1:]):
                p_in = c[a[1]]
                p_out = c[b[1]]
                ret = (p_out / p_in - 1) * 100 - L.RT_COST * 100
                if a[0] == "H":      # 고점 → 다음 저점 : 인버스(숏) 방향이지만 가격은 정방향으로 계산
                    pass
                usd = abs(ret) / 100 * BUDGET
                dsum += usd
                legs += 1
        per_day[d] = round(dsum, 2)
        tot += dsum
    return {"threshold_pct": thr, "legs": legs, "usd_at_150": round(tot, 2), "per_day": per_day,
            "per_day_avg": round(tot / max(len(days), 1), 2)}


def main() -> int:
    panel = L.panel(period="60d")
    days60 = sorted(panel["SOXL"]["session"].unique())
    days60 = [d for d in days60 if d >= days60[0]]
    res5, res60 = {}, {}
    for name in CONFIGS:
        res5[name] = run_intraday(name, DAYS5, panel)
        res60[name] = run_intraday(name, days60, panel)
    res5["C5"] = run_gap(DAYS5, panel)
    res60["C5"] = run_gap(days60, panel)

    # 파라미터 민감도(과최적화 점검) — 60일 전체 기준
    sens: Dict[str, Any] = {}
    grid = {
        "C1": [{"z": 2.5, "stop_atr": 1.0, "trail_atr": None, "max_hold": 6, "exit_mode": "vwap"},
               {"z": 1.8, "stop_atr": 1.5, "trail_atr": None, "max_hold": 9, "exit_mode": "vwap"}],
        "C2": [{"adx_min": 22.0, "rvol_min": 1.0, "stop_atr": 1.5, "trail_atr": 1.5},
               {"adx_min": 20.0, "rvol_min": 0.8, "stop_atr": 1.5, "trail_atr": 1.5}],
        "C3": [{"rsi_lo": 10.0, "rsi_hi": 90.0, "stop_atr": 1.5, "max_hold": 12, "exit_mode": "rsi50"},
               {"rsi_lo": 5.0, "rsi_hi": 95.0, "stop_atr": 2.0, "max_hold": 18, "exit_mode": "rsi50"}],
        "C4": [{"stop_atr": 1.5, "trail_atr": 1.5, "exit_mode": "don6"},
               {"stop_atr": 1.0, "trail_atr": 2.0, "exit_mode": "don6"}],
    }
    for name, variants in grid.items():
        sens[name] = []
        for v in variants:
            tr = []
            for under, inv in PAIRS:
                tr += S.simulate(name, panel[under], panel[inv], days60, v, under, inv, BUDGET)["trades"]
            st = merge(tr)
            sens[name].append({"params": v, "n": st["n"], "pnl_usd": st["pnl_usd"], "win_pct": st["win_pct"],
                               "pf": st["pf"], "avg_pct": st["avg_pct"]})
    sens["C5"] = []
    for sa in (2.0, 5.0):
        tr = []
        for under, inv in PAIRS:
            tr += S.simulate_gap_day(panel[under], panel[inv], days60, stop_atr=sa, budget=BUDGET,
                                     under_sym=under, inv_sym=inv)["trades"]
        st = merge(tr)
        sens["C5"].append({"params": {"stop_atr": sa}, "n": st["n"], "pnl_usd": st["pnl_usd"],
                           "win_pct": st["win_pct"], "pf": st["pf"], "avg_pct": st["avg_pct"]})

    # 수수료 민감도 — 왕복 0.12%(절반) / 0.36%
    cost_sens: Dict[str, Any] = {}
    for rt_label, rt in (("rt_0.12pct", 0.0012), ("rt_0.36pct", 0.0036)):
        row = {}
        for name in list(CONFIGS) + ["C5"]:
            tr = []
            for under, inv in PAIRS:
                if name == "C5":
                    tr += S.simulate_gap_day(panel[under], panel[inv], days60, stop_atr=C5["stop_atr"],
                                             budget=BUDGET, rt=rt, under_sym=under, inv_sym=inv)["trades"]
                else:
                    tr += S.simulate(name, panel[under], panel[inv], days60, CONFIGS[name], under, inv,
                                     BUDGET, rt=rt)["trades"]
            st = merge(tr)
            row[name] = {"n": st["n"], "pnl_usd": st["pnl_usd"], "avg_pct": st["avg_pct"], "pf": st["pf"]}
        cost_sens[rt_label] = row

    # 현행 엔진 참조(5일)
    core_ref = {}
    try:
        with open(os.path.join(DATA, "research_10m_5d_20261118.json"), encoding="utf-8") as f:
            core_ref = json.load(f)["core_current"]["stats"]
    except Exception:
        pass

    out = {
        "generated_at_kst": pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S"),
        "budget_usd": BUDGET, "cost_round_trip_pct": L.RT_COST * 100,
        "days_5": DAYS5, "days_60": [days60[0], days60[-1], len(days60)],
        "core_current_5d": core_ref,
        "results_5d": {k: {kk: vv for kk, vv in v.items() if kk != "trades"} | {"trades": v["trades"]}
                       for k, v in res5.items()},
        "results_60d": {k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in res60.items()},
        "oracle_5d": oracle(DAYS5, panel), "oracle_60d": oracle(days60, panel),
        "param_sensitivity_60d": sens, "cost_sensitivity_60d": cost_sens,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved", OUT)
    hdr = f"{'strat':4s} {'5d n':>5s} {'5d $':>8s} {'5d win':>7s} {'5d pf':>6s} | {'60d n':>6s} {'60d $':>9s} {'60d win':>8s} {'60d pf':>7s} {'60d avg%':>9s} {'60d t':>6s} {'fee%':>5s}"
    print(hdr)
    for k in res5:
        a, b = res5[k]["stats"], res60[k]["stats"]
        print(f"{k:4s} {a['n']:5d} {a['pnl_usd']:8.2f} {str(a['win_pct']):>7s} {str(a['pf']):>6s} | "
              f"{b['n']:6d} {b['pnl_usd']:9.2f} {str(b['win_pct']):>8s} {str(b['pf']):>7s} "
              f"{str(b['avg_pct']):>9s} {str(b['t_stat']):>6s} {str(b['fee_share_pct']):>5s}")
    print("oracle 5d", out["oracle_5d"]["usd_at_150"], "60d", out["oracle_60d"]["usd_at_150"],
          "legs", out["oracle_60d"]["legs"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
