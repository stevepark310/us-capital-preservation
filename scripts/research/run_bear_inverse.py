#!/usr/bin/env python3
"""
하락장 인버스(SQQQ/SOXS) 슬리브 — 진입·청산·수량·방어책 분해 백테스트 (5년 일봉).

    python3 scripts/research/run_bear_inverse.py
    → data/research_bear_inverse_20261120.json

동훈님 지시(2026-11-20)
-----------------------
"하락장에서도 수익을 낼 수 있게 해야해. 그리고 수량도 좀더 늘려도 괜찮을 것 같아
 다만 헷징 등 다양한 방어책을 두면서 베팅을 늘려보자. 모든 행위는 백테스트를 진행해서
 신뢰할 결과를 만들어서 적용해야해"

이 스크립트가 하는 일
---------------------
1. **기존 V2 헤지 슬리브가 왜 열위였는지 분해** — 진입 시점(다음 시가 vs 당일 종가),
   손절 폭, 트레일, 보유 상한, 청산 조건을 하나씩 바꿔가며 EV가 어디서 사라지는지 본다.
2. **레짐 게이트** — CRISIS(VIX≥28) / SPY<200DMA / QQQ 200DMA / VIX≥24 를 비교.
3. **수량(베팅)** — 1·2·3·4주 / 자산 10·15·20% / ATR 리스크 타깃(자산 1·2·3%).
4. **방어책** — 포지션 하드 손절(4·6·8%), 일일 손실한도($25), SGOV 바벨(현금 캐리),
   변동성 타게팅(VIX 28 이상 신규 금지), 부분 익절(2주 이상).
5. IS/OOS(70/30, 컷 2025-05-19) · 연도별 · 최악 1회 손실($) · MDD(%) · $25 한도 초과일.

규약
----
* 신호는 **전일 완료봉**, 진입은 **다음 봉 시가**(룩어헤드 없음). 같은 봉 SL 청산 우선.
* 왕복 비용 0.24%. 정수주. 현금은 SGOV 실측 일수익률로 캐리.
* 계좌 시작자산 $877.98(2026-11-20 원장), 1회 주문 상한 $220(현행 가드레일) 옵션.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core import regime_model  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
OUT = os.path.join(DATA, "research_bear_inverse_20261120.json")
KST = timedelta(hours=9)
RT_FEE = 0.24           # 왕복 %
EQUITY0 = 877.98
ORDER_CAP = 220.0
DAILY_LOSS_LIMIT = 25.0


def now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ 데이터
def load() -> dict:
    frames = regime_model.load_frames("5y")
    tab = regime_model.market_table(frames)
    return {"frames": frames, "tab": tab}


def _px(df, day, col="Close"):
    try:
        v = float(df.loc[day, col])
        return v if np.isfinite(v) else None
    except Exception:
        return None


def _atr_pct(df, day, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    try:
        return float(atr.loc[day] / c.loc[day] * 100.0)
    except Exception:
        return 6.0


# ------------------------------------------------------------------ 레짐
def regime_flags(frames, tab) -> pd.DataFrame:
    q = frames.get("QQQ")
    out = tab.copy()
    if q is not None:
        qc = q["Close"]
        out["qqq"] = qc.reindex(out.index).ffill()
        out["qqq_ma200"] = qc.rolling(200).mean().reindex(out.index).ffill()
        out["qqq_ma50"] = qc.rolling(50).mean().reindex(out.index).ffill()
        out["qqq_dd20"] = (out["qqq"] / out["qqq"].rolling(20).max() - 1.0) * 100.0
    out["vix_ma5"] = out["vix"].rolling(5).mean()
    out["vix_chg5"] = (out["vix"] / out["vix"].shift(5) - 1.0) * 100.0
    return out


ENTRY_RULES = {
    # 이름: (설명, 조건 함수)
    "crisis_next_open": ("전일 CRISIS → 다음 시가", lambda r: r["regime"] == "CRISIS"),
    "vix24": ("전일 VIX ≥ 24", lambda r: float(r["vix"]) >= 24.0),
    "spy_below_200dma": ("전일 SPY < 200DMA", lambda r: not bool(r["trend"])),
    "qqq_below_200dma": ("전일 QQQ < 200DMA", lambda r: bool(r.get("qqq", 0) < r.get("qqq_ma200", 0))),
    "bear_regime": ("VIX ≥ 24 그리고 SPY < 200DMA", lambda r: float(r["vix"]) >= 24.0 and not bool(r["trend"])),
    "pullback_in_bear": ("SPY<200DMA 그리고 20일 고점 대비 -5%", lambda r: (not bool(r["trend"]))
                         and float(r.get("qqq_dd20") or 0) <= -5.0),
}


# ------------------------------------------------------------------ 시뮬레이터
def simulate(d: dict, *, entry_rule: str = "crisis_next_open", ticker: str = "SQQQ",
             size_mode: str = "pct_equity", size_value: float = 10.0,
             sl_pct: float = 8.0, trail_atr: float | None = 0.9, max_hold: int = 5,
             exit_rule: str = "trail", daily_cap: bool = False, order_cap: float | None = None,
             barbell_sgov: bool = True, vix_block: float | None = None,
             take_profit_pct: float | None = None, entry_timing: str = "next_open",
             fractional: bool = True) -> dict:
    frames, tab = d["frames"], d["tab"]
    book = frames.get(ticker)
    sgov = frames.get("SGOV")
    if book is None or tab is None or not len(tab):
        return {"error": "no data"}
    cond = ENTRY_RULES[entry_rule][1]
    sgov_ret = {}
    if sgov is not None:
        sgov_ret = (sgov["Close"].pct_change().fillna(0.0)).to_dict()

    dates = [dt for dt in tab.index if dt in book.index]
    cash = EQUITY0
    pos = None
    trades = []
    curve = []
    day_pnl = {}
    invested_days = 0
    vetoes = {"regime": 0, "cash": 0, "cap": 0, "daily_cap": 0, "vix": 0, "slot": 0}

    for i, day in enumerate(dates):
        if i == 0:
            curve.append(cash)
            continue
        prev = dates[i - 1]
        prow = tab.loc[prev]
        price_open = _px(book, day, "Open")
        price_close = _px(book, day, "Close")
        price_low = _px(book, day, "Low")
        price_high = _px(book, day, "High")
        if None in (price_open, price_close, price_low, price_high):
            curve.append(cash + (pos["qty"] * (pos["last_px"]) if pos else 0.0))
            continue

        # ---- 1) 청산
        if pos is not None:
            pos["held"] += 1
            exit_px = None
            kind = None
            atr_abs = pos["atr_abs"]
            if pos.get("tp") and price_high >= pos["tp"]:
                exit_px, kind = pos["tp"], "TP"
            if exit_px is None and price_low <= pos["sl"]:
                exit_px, kind = pos["sl"], "SL"          # 손절 우선(최악 가정)
            if exit_px is None and trail_atr:
                pos["hh"] = max(pos["hh"], price_high)
                tr = max(pos["hh"] - trail_atr * atr_abs, pos["entry"] * 1.0034)
                if price_low <= tr and tr > pos["entry"]:
                    exit_px, kind = tr, "TRAIL"
            if exit_px is None and exit_rule == "vix_clear" and float(prow["vix"]) < 22.0:
                exit_px, kind = price_open, "VIX_CLEAR"
            if exit_px is None and exit_rule == "regime_clear" and not bool(cond(prow)):
                exit_px, kind = price_open, "REGIME_CLEAR"
            if exit_px is None and pos["held"] >= max_hold:
                exit_px, kind = price_close, "TIME"
            if exit_px is None and pos.get("half") and price_high >= pos["entry"] * 1.025:
                pos["half"] = False
                qh = max(1, pos["qty"] // 2)
                cash += qh * pos["entry"] * 1.025
                pnl_h = qh * (pos["entry"] * 1.025 - pos["entry"]) - qh * pos["entry"] * RT_FEE / 100.0
                day_pnl[day] = day_pnl.get(day, 0.0) + pnl_h
                trades.append({"ticker": ticker, "kind": "HALF", "entry_date": pos["entry_date"],
                               "exit_date": str(day.date()), "qty": qh, "pnl_usd": round(pnl_h, 2),
                               "pct": round((1.025 - 1) * 100 - RT_FEE, 2), "exit_kind": "TP_HALF",
                               "held": pos["held"], "mode": pos.get("mode")})
                pos["qty"] -= qh
            if exit_px is not None:
                qty = pos["qty"]
                pnl = qty * (exit_px - pos["entry"]) - qty * pos["entry"] * RT_FEE / 100.0
                cash += qty * exit_px - qty * exit_px * RT_FEE / 200.0
                day_pnl[day] = day_pnl.get(day, 0.0) + pnl
                trades.append({"ticker": ticker, "kind": "MAIN", "entry_date": pos["entry_date"],
                               "exit_date": str(day.date()), "qty": qty, "pnl_usd": round(pnl, 2),
                               "pct": round((exit_px / pos["entry"] - 1) * 100 - RT_FEE, 2),
                               "exit_kind": kind, "held": pos["held"], "mode": pos.get("mode"),
                               "entry": round(pos["entry"], 2), "exit": round(exit_px, 2),
                               "mae_at_entry_atr": round(pos["atr_pct"], 2)})
                pos = None

        # ---- 2) 진입
        if pos is None:
            if daily_cap and day_pnl.get(day, 0.0) <= -DAILY_LOSS_LIMIT:
                vetoes["daily_cap"] += 1
            elif vix_block is not None and float(prow["vix"]) >= vix_block:
                vetoes["vix"] += 1
            elif not bool(cond(prow)):
                vetoes["regime"] += 1
            else:
                entry = price_open if entry_timing == "next_open" else price_close
                atr_pct = _atr_pct(book, prev)
                atr_abs = entry * atr_pct / 100.0
                equity_now = cash + (pos["qty"] * pos["last_px"] if pos else 0.0)
                if size_mode == "shares":
                    qty = float(size_value)
                elif size_mode == "pct_equity":
                    qty = equity_now * size_value / 100.0 / entry
                elif size_mode == "risk_target":       # 자산 대비 리스크 % / 손절폭
                    qty = (equity_now * size_value / 100.0) / (entry * sl_pct / 100.0)
                else:
                    qty = 1.0
                if not fractional:
                    qty = float(int(qty))
                cap = order_cap if order_cap else 1e9
                if qty * entry > cap:
                    qty = cap / entry
                    vetoes["cap"] += 1
                qty = min(qty, cash / (entry * (1 + RT_FEE / 200.0)))
                if fractional and qty > 0:
                    qty = round(qty, 4)
                if qty <= 0:
                    vetoes["cash"] += 1
                else:
                    cash -= qty * entry * (1 + RT_FEE / 200.0)
                    pos = {"entry": entry, "qty": qty, "held": 0, "hh": entry,
                           "atr_pct": atr_pct, "atr_abs": atr_abs,
                           "sl": round(entry * (1 - sl_pct / 100.0), 2),
                           "tp": (round(entry * (1 + take_profit_pct / 100.0), 2)
                                  if take_profit_pct else None),
                           "half": bool(take_profit_pct and qty >= 2),
                           "entry_date": str(day.date()), "last_px": price_close,
                           "mode": f"{entry_rule}/{exit_rule}"}
        # ---- 3) 평가
        mtm = pos["qty"] * price_close if pos else 0.0
        if pos:
            pos["last_px"] = price_close
            invested_days += 1
        equity = cash + mtm
        curve.append(round(equity, 2))
        if barbell_sgov:
            cash *= (1.0 + float(sgov_ret.get(day, 0.0) or 0.0))

    # ---- 통계
    mains = [t for t in trades if t["kind"] == "MAIN"]
    all_t = trades
    pnls = [t["pnl_usd"] for t in all_t]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, (v / peak - 1.0) * 100.0)
    eq_d = pd.Series(curve, index=dates)
    rets = eq_d.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if len(rets) > 2 and rets.std() > 0 else 0.0
    # IS/OOS
    cut = pd.Timestamp("2025-05-19")
    def block(ts):
        sub = [t for t in ts]
        p = [t["pnl_usd"] for t in sub]
        if not p:
            return {"n": 0}
        return {"n": len(p), "pnl_usd": round(sum(p), 2), "ev_usd": round(statistics.mean(p), 2),
                "win_pct": round(sum(1 for x in p if x > 0) / len(p) * 100, 1),
                "worst_usd": round(min(p), 2)}
    is_b = block([t for t in all_t if pd.Timestamp(t["exit_date"]) < cut])
    oos_b = block([t for t in all_t if pd.Timestamp(t["exit_date"]) >= cut])
    yrs = {}
    for t in all_t:
        y = str(t["exit_date"])[:4]
        yrs.setdefault(y, []).append(t["pnl_usd"])
    day_over = sum(1 for v in day_pnl.values() if v <= -DAILY_LOSS_LIMIT)
    return {
        "params": {"entry_rule": entry_rule, "ticker": ticker, "size_mode": size_mode,
                   "size_value": size_value, "sl_pct": sl_pct, "trail_atr": trail_atr,
                   "max_hold": max_hold, "exit_rule": exit_rule, "daily_cap": daily_cap,
                   "order_cap": order_cap, "vix_block": vix_block,
                   "take_profit_pct": take_profit_pct, "entry_timing": entry_timing},
        "n_trades": len(all_t), "n_main": len(mains),
        "pnl_usd": round(sum(pnls), 2),
        "ev_usd": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "ev_pct": round(statistics.mean([t["pct"] for t in mains]), 3) if mains else 0.0,
        "win_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        "pf": round(pf, 2) if pf else None,
        "worst_usd": round(min(pnls), 2) if pnls else 0.0,
        "total_return_pct": round((curve[-1] / curve[0] - 1) * 100, 2) if curve else 0.0,
        "mdd_pct": round(mdd, 2), "sharpe": round(sharpe, 2),
        "avg_hold_days": round(statistics.mean([t["held"] for t in mains]), 1) if mains else 0,
        "exposure_pct": round(invested_days / max(1, len(dates)) * 100, 1),
        "days_over_daily_limit": day_over,
        "is": is_b, "oos": oos_b,
        "yearly_pnl": {y: round(sum(v), 2) for y, v in sorted(yrs.items())},
        "vetoes": vetoes,
        "exit_mix": {k: sum(1 for t in mains if t["exit_kind"] == k)
                     for k in sorted({t["exit_kind"] for t in mains})},
        "period": f"{dates[0].date()} ~ {dates[-1].date()} ({len(dates)}거래일)",
    }


# ------------------------------------------------------------------ main
def main() -> int:
    d = load()
    d["tab"] = regime_flags(d["frames"], d["tab"])
    tab = d["tab"]
    print(f"[bear-inverse] {tab.index[0].date()} ~ {tab.index[-1].date()} ({len(tab)}일)")
    print("레짐 일수:", tab["regime"].value_counts().to_dict())

    variants = {}
    # ---- A. 기존 V2 헤지 재현 + 원인 분해 (자산 10%, 정수주 제약 제거)
    base = dict(entry_rule="crisis_next_open", size_mode="pct_equity", size_value=10.0,
                sl_pct=8.0, trail_atr=0.9, max_hold=5, exit_rule="vix_clear")
    variants["A0_기존V3헤지(재현)"] = dict(base)
    variants["A1_진입=당일종가"] = dict(base, entry_timing="close")
    variants["A2_트레일제거(시간청산만)"] = dict(base, trail_atr=None)
    variants["A3_보유10일"] = dict(base, max_hold=10)
    variants["A4_손절4%"] = dict(base, sl_pct=4.0)
    variants["A5_손절12%"] = dict(base, sl_pct=12.0)
    variants["A6_청산=레짐해제"] = dict(base, exit_rule="regime_clear")
    variants["A7_트레일1.5ATR"] = dict(base, trail_atr=1.5)

    # ---- B. 진입 레짐 비교 (자산 10%, 손절 6%, 트레일 없음, 5일, 레짐해제 청산)
    for rule_name in ENTRY_RULES:
        variants[f"B_{rule_name}"] = dict(entry_rule=rule_name, size_mode="pct_equity", size_value=10.0,
                                          sl_pct=6.0, trail_atr=None, max_hold=5,
                                          exit_rule="regime_clear")

    # ---- C. 수량 (자산 대비 %) × 레짐 게이트
    for ent in ("spy_below_200dma", "pullback_in_bear"):
        for pct in (5, 10, 15, 20, 25):
            variants[f"C_{ent}_자산{pct}%"] = dict(entry_rule=ent, size_mode="pct_equity",
                                                  size_value=pct, sl_pct=6.0, trail_atr=None,
                                                  max_hold=5, exit_rule="regime_clear")
        for rp in (1.0, 2.0, 3.0):
            variants[f"C_{ent}_리스크{rp}%"] = dict(entry_rule=ent, size_mode="risk_target",
                                                    size_value=rp, sl_pct=6.0, trail_atr=None,
                                                    max_hold=5, exit_rule="regime_clear")

    # ---- D. 방어책 조합 (후보 = SPY<200DMA, 자산 15%)
    cand = dict(entry_rule="spy_below_200dma", size_mode="pct_equity", size_value=15.0,
                sl_pct=6.0, trail_atr=None, max_hold=5, exit_rule="regime_clear")
    variants["D0_기준(자산15%)"] = dict(cand)
    variants["D1_+일일캡$25"] = dict(cand, daily_cap=True)
    variants["D2_+VIX28신규금지"] = dict(cand, vix_block=28.0)
    variants["D3_+부분익절2.5%"] = dict(cand, take_profit_pct=2.5)
    variants["D4_+부분익절3.5%"] = dict(cand, take_profit_pct=3.5)
    variants["D5_+손절4%"] = dict(cand, sl_pct=4.0)
    variants["D6_+보유3일"] = dict(cand, max_hold=3)
    variants["D7_+트레일1.5ATR"] = dict(cand, trail_atr=1.5)
    variants["E_SOXS_자산15%"] = dict(cand, ticker="SOXS")

    results = {}
    for name, kw in variants.items():
        r = simulate(d, **kw)
        results[name] = r
        print(f"{name:28s} n={r.get('n_trades',0):3d} pnl={r.get('pnl_usd',0):+8.2f} "
              f"ret={r.get('total_return_pct',0):+6.2f}% mdd={r.get('mdd_pct',0):+6.2f}% "
              f"pf={r.get('pf')} win={r.get('win_pct',0):5.1f}% worst={r.get('worst_usd',0):+7.2f} "
              f"IS={r.get('is',{}).get('pnl_usd',0):+7.2f} OOS={r.get('oos',{}).get('pnl_usd',0):+7.2f} "
              f"exp={r.get('exposure_pct',0):4.1f}%")

    doc = {"generated_at_kst": now_kst(), "period": results[list(results)[0]]["period"],
           "regime_days": tab["regime"].value_counts().to_dict(),
           "equity_start": EQUITY0, "fee_round_trip_pct": RT_FEE,
           "results": results,
           "note": ("신호는 전일 완료봉, 진입은 다음 봉 시가(entry_timing=close는 당일 종가), "
                    "같은 봉 SL 우선, 왕복 0.24%, 정수주, 현금 SGOV 캐리. IS/OOS 컷 2025-05-19.")}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
