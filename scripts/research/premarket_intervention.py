#!/usr/bin/env python3
"""프리장 개입 전략 백테스트 — 어떤 조건에서만 기대값이 양(+)인가 (2026-11-21).

동훈님 지시(2026-11-21 13:53): "필요시 프리장이나 본장에 개입해서 바로바로 매매할 수 있게."
그러려면 먼저 **프리장 개입이 돈이 되는 조건**을 실측해야 한다. 2026-11-18 연구는
"조건 없는 프리장 진입은 비용만큼 잃는다(−0.35~−0.60%/건)"까지만 답했다. 이 스크립트는
그 위에 한 걸음 더 간다 — ① 승인된 신호(DIP_SWING_DAILY)가 뜬 날 ② 프리장에 미리 들어가는
것과 ③ 정규장 시가에 들어가는 것을 **같은 날·같은 청산가·같은 비용(왕복 0.44%)** 으로 비교하고,
진입 시각(04:00/06:00/08:00/09:30 ET)과 조건(국면·VIX·갭 추격·갭 하락)별로 쪼갠다.

데이터·규칙
-----------
* 프리장 포함 60분봉 `data_cache/research/{TK}_60m_730d_pre1.pkl` (2023-12-20~2026-11-17, ET 기준).
* 신호는 **전일 완료 일봉**(RSI2≤20 · %B≤0.15 · RVOL≥1.0) — 진입 시점에 알 수 있는 값만.
* 진입: 해당 시각 봉의 시가, 청산: 같은 세션 15:30 봉 종가(=16:00 종가). 1세션 보유 변형도 보고.
* 비용: 왕복 0.44%(프리장 가정) 기본, 민감도 0.34/0.59% 함께.
* 같은 봉에서 진입·청산만 하고, 손절·익절 브래킷은 적용하지 않는다(순수 '진입 시각' 효과 분리).

한계(먼저 밝힘)
---------------
* 60분봉 프리장 구간의 거래량은 대부분 0으로 기록돼 **유동성 필터를 검증할 수 없다**(85.7% 0).
  그래서 프리장 방어는 실측이 아니라 지정가 슬리피지 상한·예산·건수 상한으로 건다.
* 3년 표본이며 3배 레버리지 ETF의 변동성 감쇠가 구간에 좌우된다. 1배(SOXX) 대조를 함께 본다.

실행
----
    python3 scripts/research/premarket_intervention.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from core import regime_model  # noqa: E402

RESEARCH = os.path.join(BASE_DIR, "data_cache", "research")
OUT = os.path.join(BASE_DIR, "docs", "premarket_intervention_20261121.json")
KST = timedelta(hours=9)

LONG = ("SOXL", "TQQQ")
HEDGE = ("SOXS", "SQQQ")
ENTRY_TIMES = ["04:00", "06:00", "08:00", "09:30"]
EXIT_BAR = "15:30"


def _now() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


def load_intraday(ticker: str) -> Optional[pd.DataFrame]:
    path = os.path.join(RESEARCH, f"{ticker}_60m_730d_pre1.pkl")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_pickle(path)
        if not isinstance(df, pd.DataFrame) or not len(df):
            return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception:
        return None


def session_prices(df: pd.DataFrame) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{세션일: {'04:00': {open, close}, ...}} — ET 기준."""
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for ts, row in df.iterrows():
        day = ts.strftime("%Y-%m-%d")
        hhmm = ts.strftime("%H:%M")
        out.setdefault(day, {})[hhmm] = {"open": float(row["Open"]), "close": float(row["Close"]),
                                         "high": float(row["High"]), "low": float(row["Low"])}
    return out


def stats(rets: List[float]) -> Dict[str, Any]:
    n = len(rets)
    if not n:
        return {"n": 0, "available": False}
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (float("inf") if wins else 0.0)
    peak, mdd, eq = 0.0, 0.0, 0.0
    for r in rets:
        eq += r
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {"n": n, "ev_pct": round(mean, 4), "t_stat": round(t, 2),
            "win_rate_pct": round(100.0 * len(wins) / n, 1),
            "profit_factor": (round(pf, 2) if pf != float("inf") else None),
            "best_pct": round(max(rets), 2), "worst_pct": round(min(rets), 2),
            "cum_pct": round(sum(rets), 2), "mdd_pct": round(mdd, 2),
            "stderr_pct": round(sd / math.sqrt(n), 4) if sd else 0.0}


def build_trades(daily: Dict[str, pd.DataFrame], intraday: Dict[str, pd.DataFrame],
                 sessions: List[str], tickers: Tuple[str, ...], cost: float,
                 hold_sessions: int = 0) -> List[Dict[str, Any]]:
    """신호가 뜬 세션마다 진입 시각별 수익률을 만든다(비용 차감 후 %)."""
    table = regime_model.market_table(daily)
    books = {tk: regime_model.with_indicators(daily[tk]) for tk in tickers if tk in daily}
    calendars = {tk: session_prices(df) for tk, df in intraday.items()}
    rows: List[Dict[str, Any]] = []
    day_list = [d.strftime("%Y-%m-%d") for d in sorted(daily["SPY"].index)]
    pos = {d: i for i, d in enumerate(day_list)}
    for day in sessions:
        if day not in pos or pos[day] == 0:
            continue
        prev_day = day_list[pos[day] - 1]
        next_days = day_list[pos[day] + 1: pos[day] + 1 + hold_sessions] if hold_sessions else []
        exit_day = next_days[-1] if next_days else day
        try:
            md = table.loc[pd.Timestamp(prev_day)]
        except KeyError:
            continue
        for tk in tickers:
            book = books.get(tk)
            cal = calendars.get(tk)
            if book is None or cal is None or pd.Timestamp(prev_day) not in book.index:
                continue
            prow = book.loc[pd.Timestamp(prev_day)]
            if not (float(prow["RSI2"]) <= 20.0 and float(prow["PCT_B"]) <= 0.15
                    and float(prow["RVOL"]) >= 1.0):
                continue
            bars = cal.get(day) or {}
            exit_bars = cal.get(exit_day) or {}
            exit_px = (exit_bars.get(EXIT_BAR) or {}).get("close")
            if not exit_px:
                continue
            prev_close = float(prow["Close"])
            rec = {"date": day, "ticker": tk, "prev_close": prev_close,
                   "regime": str(md["regime"]), "vix": float(md["vix"]),
                   "spy_trend": bool(md["trend"]), "exit_day": exit_day}
            for hhmm in ENTRY_TIMES:
                bar = bars.get(hhmm)
                if not bar or not bar.get("open"):
                    continue
                entry = float(bar["open"])
                gross = (exit_px / entry - 1.0) * 100.0
                rec[f"gap_{hhmm}_pct"] = round((entry / prev_close - 1.0) * 100.0, 3)
                rec[hhmm] = round(gross - cost, 4)
                rec[f"gross_{hhmm}"] = round(gross, 4)
            if all(h in bars for h in ENTRY_TIMES):
                rows.append(rec)
    return rows


def _subset(rows: List[Dict[str, Any]], pred) -> List[Dict[str, Any]]:
    return [r for r in rows if pred(r)]


def arm(rows: List[Dict[str, Any]], entry: str, pred=None) -> Dict[str, Any]:
    sel = _subset(rows, pred) if pred else rows
    vals = [r[entry] for r in sel if entry in r]
    out = stats(vals)
    out.update({"entry": entry, "n_days": len(sel)})
    if entry != "09:30":
        paired = [r[entry] - r["09:30"] for r in sel if entry in r and "09:30" in r]
        out["vs_regular_open"] = stats(paired)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=0.44)
    args = ap.parse_args(argv)

    daily = regime_model.load_frames("5y")
    intraday = {tk: load_intraday(tk) for tk in LONG + HEDGE}
    intraday = {k: v for k, v in intraday.items() if v is not None}
    if not intraday or not daily:
        print("데이터 없음")
        return 1
    sessions = sorted(set.intersection(*[set(session_prices(v).keys()) for v in intraday.values()]))
    sessions = [s for s in sessions if s in {d.strftime("%Y-%m-%d") for d in daily["SPY"].index}]
    split = int(len(sessions) * 0.7)
    cut = sessions[split - 1] if sessions else ""

    def _run(tickers, horizon, label):
        rows = build_trades(daily, intraday, sessions, tuple(tickers), args.cost, horizon)
        is_rows = [r for r in rows if r["date"] <= cut]
        oos_rows = [r for r in rows if r["date"] > cut]
        conditions = {
            "all_signals": (lambda r: True, "조건 없음(승인된 DIP 신호 전부)"),
            "regime_on_neutral": (lambda r: r["regime"] in ("RISK_ON", "NEUTRAL"),
                                  "전일 레짐 RISK_ON·NEUTRAL"),
            "vix_16_24": (lambda r: 16.0 <= r["vix"] <= 24.0, "전일 VIX 16~24"),
            "no_chase_1pct": (lambda r: r.get("gap_04:00_pct", 99) <= 1.0,
                              "04:00 프리장 가격이 전일 종가 대비 +1% 이하(추격 금지)"),
            "gap_down": (lambda r: r.get("gap_04:00_pct", 99) <= 0.0,
                         "04:00 프리장 가격이 전일 종가 이하(갭 하락 매수)"),
            "regime_and_no_chase": (lambda r: r["regime"] in ("RISK_ON", "NEUTRAL")
                                    and r.get("gap_04:00_pct", 99) <= 1.0,
                                    "RISK_ON·NEUTRAL + 추격 금지"),
        }
        out = {}
        for cname, (pred, desc) in conditions.items():
            out[cname] = {"desc": desc,
                          "all": arm(rows, "04:00", pred),
                          "oos": arm(oos_rows, "04:00", pred),
                          "is": arm(is_rows, "04:00", pred)}
        by_entry = {hhmm: {"all": arm(rows, hhmm), "oos": arm(oos_rows, hhmm),
                           "is": arm(is_rows, hhmm)} for hhmm in ENTRY_TIMES}
        return {"label": label, "tickers": list(tickers), "horizon_sessions": horizon,
                "n_signal_rows": len(rows), "is_rows": len(is_rows), "oos_rows": len(oos_rows),
                "conditions": out, "by_entry_time": by_entry}

    report = {
        "generated_at_kst": _now(),
        "engine": "PREMARKET_INTERVENTION_V1",
        "cost_round_trip_pct": args.cost,
        "sessions": {"n": len(sessions), "first": sessions[0] if sessions else None,
                     "last": sessions[-1] if sessions else None,
                     "is_oos_cut": cut},
        "signal": "DIP_SWING_DAILY (전일 일봉 RSI2<=20 · %B<=0.15 · RVOL>=1.0)",
        "limitations": [
            "60분봉 프리장 거래량이 대부분 0으로 기록돼(85.7%) 유동성 필터는 검증 불가 — 방어는 지정가·예산·건수로 건다.",
            "3년 표본, 3배 레버리지 ETF의 변동성 감쇠가 구간에 좌우된다.",
            "같은 봉 진입·청산만 측정(브래킷·손절은 분리)해 '진입 시각' 효과만 본다.",
        ],
        "long_same_day": _run(LONG, 0, "DIP_SWING_DAILY 동일 세션 청산(SOXL·TQQQ)"),
        "long_next_day": _run(LONG, 1, "DIP_SWING_DAILY 1세션 보유(SOXL·TQQQ)"),
        "hedge_same_day": _run(HEDGE, 0, "헤지(SOXS·SQQQ) 동일 세션 청산"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    for key in ("long_same_day", "long_next_day", "hedge_same_day"):
        blk = report[key]
        print(f"\n== {blk['label']} · 신호 {blk['n_signal_rows']}건 (IS {blk['is_rows']} / OOS {blk['oos_rows']})")
        print(f"{'조건':22s} {'n':>4} {'EV%':>7} {'t':>6} {'승률%':>6} {'PF':>5} {'04:00 vs 09:30 ΔEV%':>20}")
        for cname, c in blk["conditions"].items():
            a, o = c["all"], c["oos"]
            d = a.get("vs_regular_open") or {}
            print(f"{cname:22s} {str(a.get('n')):>4} {str(a.get('ev_pct')):>7} {str(a.get('t_stat')):>6} "
                  f"{str(a.get('win_rate_pct')):>6} {str(a.get('profit_factor')):>5} "
                  f"{str(d.get('ev_pct'))+' (t '+str(d.get('t_stat'))+')':>20}")
        print("  진입 시각별(전체 신호):")
        for hhmm, e in blk["by_entry_time"].items():
            a = e["all"]
            print(f"    {hhmm}  n {a.get('n')} · EV {a.get('ev_pct')}% · t {a.get('t_stat')} · "
                  f"승률 {a.get('win_rate_pct')}% · PF {a.get('profit_factor')} · 누적 {a.get('cum_pct')}%")
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
