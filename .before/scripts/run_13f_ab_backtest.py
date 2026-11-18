#!/usr/bin/env python3
"""
13F 스마트머니 기반 매매 알고리즘 개선안 A/B 백테스트

동훈님 원칙: "수치·코드·로직 기반, 왕복 0.24% 마찰을 뺀 실측 백테스트로 검증되는 것만
채택하고, 기각된 아이디어는 기각 사유와 수치를 그대로 보고한다."

검증 1 — 현금화/레버리지 오버레이 (SOXL·TQQQ, DIP_SWING 동일 규칙, 2년 일봉)
  A0 baseline            : 현행 규격(1회 25% · 리스크 2% · 최대 2종목)
  A1 vix_tiered          : VIX <20 →1.0 / 20~24 →0.75 / 24~28 →0.5 / ≥28 →0 (신호일 VIX)
  A2 cash_index_static   : 13F 현금화 압력지수 85.4에 대응하는 고정 0.7배 축소
  A3 semis_split         : SMH 20일 수익률 < 0 이면 SOXL만 0.5배(반도체 의견 분열 프록시)
  A4 combined            : min(A1, A3)

검증 2 — 13F 매집 종목의 DIP_SWING 유효성 (개별 2년, 그리고 포트폴리오 확장)
  GOOGL NFLX ALC DDOG U STX LEN MSFT AMZN GLD SOXX + 기준선 SOXL·TQQQ
  · 신호: RSI2 ≤ 20 · %B ≤ 0.15 · RVOL ≥ 1.0 (승인 규격 그대로)
  · 체결: 신호 다음 세션 시가, ATR×1.5 대칭 TP/SL, 최대 3세션, 동시 터치는 손절 우선
  · 마찰: 왕복 0.24% (NH 0.09% + 슬리피지 0.15%)
  · 과적합 점검: 기간 반분(전반/후반) 일관성 + 파라미터 ±민감도

결과: data/13f_ab_backtest.json + 콘솔 요약
실행: python3 scripts/run_13f_ab_backtest.py [--refresh]
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import swing_engine  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(BASE_DIR, "data_cache")
CACHE = os.path.join(CACHE_DIR, "13f_ab_daily.pkl")
OUT = os.path.join(DATA, "13f_ab_backtest.json")
FEE = swing_engine.ROUND_TRIP_FEE
BASE_UNIVERSE = ["SOXL", "TQQQ"]
CANDIDATES = ["GOOGL", "NFLX", "ALC", "DDOG", "U", "STX", "LEN", "MSFT", "AMZN", "GLD", "SOXX"]
EXTRA = ["SMH", "^VIX"]
THIRTEENF = os.path.join(DATA, "superinvestor_13f.json")


def _load_daily(refresh: bool = False) -> Dict[str, pd.DataFrame]:
    syms = BASE_UNIVERSE + CANDIDATES + EXTRA
    if not refresh and os.path.exists(CACHE):
        try:
            obj = pickle.load(open(CACHE, "rb"))
            age_h = (datetime.now() - datetime.fromisoformat(obj["fetched_at"])).total_seconds() / 3600
            if age_h < 12 and set(syms).issubset(obj["frames"].keys()):
                return obj["frames"]
        except Exception:
            pass
    import yfinance as yf
    raw = yf.download(syms, period="2y", interval="1d", progress=False, auto_adjust=False, group_by="ticker")
    frames: Dict[str, pd.DataFrame] = {}
    for s in syms:
        try:
            df = raw[s].dropna(subset=["Close"]).copy()
            if len(df) > 60:
                frames[s] = df
        except Exception:
            continue
    os.makedirs(CACHE_DIR, exist_ok=True)
    pickle.dump({"fetched_at": datetime.now().isoformat(), "frames": frames}, open(CACHE, "wb"))
    return frames


def _vix_series(daily: Dict[str, pd.DataFrame]) -> Optional[pd.Series]:
    v = daily.get("^VIX")
    if v is None:
        return None
    s = v["Close"]
    if hasattr(s, "columns"):
        s = s.iloc[:, 0]
    return s.astype(float)


def _smh_mom(daily: Dict[str, pd.DataFrame]) -> Optional[pd.Series]:
    d = daily.get("SMH")
    if d is None:
        return None
    c = d["Close"]
    if hasattr(c, "columns"):
        c = c.iloc[:, 0]
    return c.astype(float).pct_change(20) * 100


# --------------------------------------------------------------- experiment 1
def _overlay_fn(name: str, vix: Optional[pd.Series], smh: Optional[pd.Series]):
    def vix_at(day):
        if vix is None:
            return None
        try:
            sub = vix[vix.index <= day]
            return float(sub.iloc[-1]) if len(sub) else None
        except Exception:
            return None

    def vix_scale(day) -> float:
        v = vix_at(day)
        if v is None:
            return 1.0
        if v >= 28:
            return 0.0
        if v >= 24:
            return 0.5
        if v >= 20:
            return 0.75
        return 1.0

    def smh_m(day) -> Optional[float]:
        if smh is None:
            return None
        try:
            sub = smh[smh.index <= day]
            return float(sub.iloc[-1]) if len(sub) else None
        except Exception:
            return None

    def fn(day, sym, sig) -> float:
        if name == "A1_vix_tiered":
            return vix_scale(day)
        if name == "A2_cash_index_static":
            return 0.7
        if name == "A3_semis_split":
            m = smh_m(day)
            return 0.5 if (m is not None and m < 0 and sym == "SOXL") else 1.0
        if name == "A4_combined":
            m = smh_m(day)
            semi = 0.5 if (m is not None and m < 0 and sym == "SOXL") else 1.0
            return min(vix_scale(day), semi)
        return 1.0

    return fn


def _metrics(r: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("total_return_pct", "cagr_pct", "mdd_pct", "win_rate_pct", "total_trades",
            "profit_factor", "avg_trade_pct", "sharpe_ratio", "period", "avg_win_pct", "avg_loss_pct")
    return {k: r.get(k) for k in keys}


# --------------------------------------------------------------- experiment 2
def _stats_from_trades(sym: str, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"ticker": sym, "n": 0, "note": "신호 없음"}
    rets = [t["ret_pct"] for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    gain = sum(wins)
    loss = abs(sum(losses))
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    return {
        "ticker": sym, "n": len(rets), "win_pct": round(len(wins) / len(rets) * 100, 1),
        "ev_pct": round(float(np.mean(rets)), 3),
        "median_pct": round(float(np.median(rets)), 3),
        "profit_factor": round(gain / loss, 2) if loss else None,
        "t_stat": round(float(np.mean(rets)) / (sd / np.sqrt(len(rets))), 2) if sd else None,
        "worst_pct": round(min(rets), 2), "best_pct": round(max(rets), 2),
        "stop_rate_pct": round(sum(1 for t in trades if t["reason"] == "STOP") / len(trades) * 100, 1),
    }


def _single_ticker_stats(daily: Dict[str, pd.DataFrame], sym: str, params: Dict[str, Any],
                         start: str = None, end: str = None) -> Dict[str, Any]:
    """신호→다음 세션 시가 진입→ATR×1.5 대칭 브래킷→최대 N세션 (일봉, 동시 터치 시 손절 우선)."""
    df = daily.get(sym)
    if df is None or len(df) < 80:
        return {"ticker": sym, "error": "데이터 부족", "n": 0, "trades": []}
    d = swing_engine.prepare(df)
    if start:
        d = d[d.index >= pd.Timestamp(start)]
    if end:
        d = d[d.index <= pd.Timestamp(end)]
    trades = []
    i = 1
    idx = list(d.index)
    while i < len(d) - 1:
        sig = swing_engine.signal_row(d.loc[idx[i]], params)
        if not sig:
            i += 1
            continue
        entry = float(d.loc[idx[i + 1], "Open"]) * (1 + FEE / 2)
        atr = float(d.loc[idx[i], "ATR"])
        tp = entry + params["tp_atr_mult"] * atr
        sl = entry - params["sl_atr_mult"] * atr
        out_px, reason, held = None, "TIME", 0
        for j in range(i + 1, min(i + 1 + params["max_hold_days"], len(d))):
            row = d.loc[idx[j]]
            hi, lo, cl = float(row["High"]), float(row["Low"]), float(row["Close"])
            held = j - i
            if lo <= sl:
                out_px, reason = sl, "STOP"
                break
            if hi >= tp:
                out_px, reason = tp, "TARGET"
                break
            out_px, reason = cl, "TIME"
        if out_px is None:
            break
        ret = (out_px / entry - 1) * 100 - FEE * 100
        trades.append({"date": str(idx[i].date()), "ret_pct": round(ret, 3), "reason": reason,
                       "held": held, "atr_pct": round(float(d.loc[idx[i], "ATR_PCT"]), 2)})
        i += 1 + held  # 보유 중 재신호는 무시(중복 진입 방지)
    out = _stats_from_trades(sym, trades)
    out["trades"] = trades
    return out


def _hourly_validation(daily: Dict[str, pd.DataFrame], params: Dict[str, Any],
                       syms: List[str], refresh: bool = False) -> Dict[str, Any]:
    """시간봉(60m)으로 브래킷 경로를 재현 — 일봉 '손절 우선' 가정이 만든 낙관/비관 편향 점검."""
    import yfinance as yf
    cache = os.path.join(CACHE_DIR, "13f_ab_hourly.pkl")
    frames: Dict[str, pd.DataFrame] = {}
    if not refresh and os.path.exists(cache):
        try:
            obj = pickle.load(open(cache, "rb"))
            if (datetime.now() - datetime.fromisoformat(obj["fetched_at"])).total_seconds() < 12 * 3600:
                frames = obj["frames"]
        except Exception:
            frames = {}
    if not frames:
        raw = yf.download(syms, period="1y", interval="600m" if False else "60m",
                          progress=False, auto_adjust=False, group_by="ticker")
        for s in syms:
            try:
                df = raw[s].dropna(subset=["Close"]).copy()
                if len(df) > 200:
                    frames[s] = df
            except Exception:
                continue
        if frames:
            pickle.dump({"fetched_at": datetime.now().isoformat(), "frames": frames}, open(cache, "wb"))

    out: Dict[str, Any] = {}
    for sym in syms:
        h = frames.get(sym)
        df = daily.get(sym)
        if h is None or df is None or len(df) < 80:
            out[sym] = {"error": "시간봉 없음"}
            continue
        d = swing_engine.prepare(df)
        sess = sorted({str(ts.date()) for ts in h.index})
        sess_set = set(sess)
        trades = []
        for i in range(1, len(d) - 1):
            day = str(d.index[i].date())
            sig = swing_engine.signal_row(d.loc[d.index[i]], params)
            if not sig:
                continue
            later = [s for s in sess if s > day]
            if not later:
                continue
            e_day = later[0]
            # 시간봉이 없는 과거 신호(1년 이전)는 '다음 세션'이 몇 달 뒤가 되어 버린다 — 버린다
            try:
                gap = (datetime.fromisoformat(e_day) - datetime.fromisoformat(day)).days
            except Exception:
                gap = 999
            if gap > 7:
                continue
            bars0 = h[[str(ts.date()) == e_day for ts in h.index]]
            if bars0.empty:
                continue
            entry = float(bars0["Open"].iloc[0]) * (1 + FEE / 2)
            atr = float(d.loc[d.index[i], "ATR"])
            tp = entry + params["tp_atr_mult"] * atr
            sl = entry - params["sl_atr_mult"] * atr
            hold_days = later[: params["max_hold_days"]]
            out_px, reason = None, "TIME"
            for day_k in hold_days:
                bars = h[[str(ts.date()) == day_k for ts in h.index]]
                hit = False
                for _ts, bar in bars.iterrows():
                    if float(bar["Low"]) <= sl:
                        out_px, reason, hit = sl, "STOP", True
                        break
                    if float(bar["High"]) >= tp:
                        out_px, reason, hit = tp, "TARGET", True
                        break
                if hit:
                    break
                out_px, reason = float(bars["Close"].iloc[-1]), "TIME"
            if out_px is None:
                continue
            trades.append({"date": day, "entry_date": e_day,
                           "ret_pct": round((out_px / entry - 1) * 100 - FEE * 100, 3), "reason": reason})
        out[sym] = _stats_from_trades(sym, trades)
        out[sym]["trades"] = trades
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    daily = _load_daily("--refresh" in args)
    print(f"data: {len(daily)} symbols, {len(daily.get('SOXL', []))} bars (SOXL)")
    vix, smh = _vix_series(daily), _smh_mom(daily)
    p = dict(swing_engine.DIP_PARAMS)

    # ---- experiment 1
    exp1: Dict[str, Any] = {}
    for name in ("A0_baseline", "A1_vix_tiered", "A2_cash_index_static", "A3_semis_split", "A4_combined"):
        fn = None if name == "A0_baseline" else _overlay_fn(name, vix, smh)
        res = swing_engine.portfolio_backtest(daily, p, size_scale_fn=fn)
        exp1[name] = _metrics(res)
        m = exp1[name]
        print(f"  {name:22s} ret {m['total_return_pct']:>7}% mdd {m['mdd_pct']:>7}% "
              f"wr {m['win_rate_pct']}% n {m['total_trades']} pf {m['profit_factor']} avg {m['avg_trade_pct']}%")
    # 1년 후반부만(최근 1년) 재검증
    half_start = str((datetime.now() - timedelta(days=365)).date())
    exp1_recent: Dict[str, Any] = {}
    for name in ("A0_baseline", "A1_vix_tiered", "A3_semis_split", "A4_combined"):
        fn = None if name == "A0_baseline" else _overlay_fn(name, vix, smh)
        res = swing_engine.portfolio_backtest(daily, p, start=half_start, size_scale_fn=fn)
        exp1_recent[name] = _metrics(res)
        print(f"  [최근1년] {name:16s} ret {res['total_return_pct']:>7}% mdd {res['mdd_pct']:>7}% n {res['total_trades']} pf {res['profit_factor']}")

    # ---- experiment 2
    per_ticker: Dict[str, Any] = {}
    for sym in BASE_UNIVERSE + CANDIDATES:
        st = _single_ticker_stats(daily, sym, p)
        half = (datetime.now() - timedelta(days=365)).date().isoformat()
        tr = st.get("trades") or []
        first = _stats_from_trades(sym, [t for t in tr if t["date"] < half])
        second = _stats_from_trades(sym, [t for t in tr if t["date"] >= half])
        st["first_year"] = {k: first.get(k) for k in ("n", "ev_pct", "win_pct", "profit_factor")}
        st["second_year"] = {k: second.get(k) for k in ("n", "ev_pct", "win_pct", "profit_factor")}
        per_ticker[sym] = st
        if st.get("n"):
            print(f"  {sym:6s} n {st['n']:>2} ev {st['ev_pct']:>7}% wr {st['win_pct']:>5}% pf {st['profit_factor']} "
                  f"t {st['t_stat']} | 1H {st['first_year']['ev_pct']} / 2H {st['second_year']['ev_pct']}")

    # 파라미터 민감도(후보 상위 종목)
    sens: Dict[str, Any] = {}
    for sym in CANDIDATES:
        variants = {}
        for label, override in {
            "rsi2_18": {"rsi2_max": 18.0}, "rsi2_22": {"rsi2_max": 22.0},
            "pctb_12": {"pct_b_max": 0.12}, "pctb_18": {"pct_b_max": 0.18},
            "hold2": {"max_hold_days": 2}, "hold5": {"max_hold_days": 5},
        }.items():
            pp = {**p, **override}
            pp["universe"] = [sym]
            st = _single_ticker_stats(daily, sym, pp)
            variants[label] = {k: st.get(k) for k in ("n", "ev_pct", "win_pct", "profit_factor")}
        sens[sym] = variants

    # 확장 유니버스 포트폴리오(양(+) 기대값 종목만, 최대 2·3종목)
    positives = [s for s, st in per_ticker.items()
                 if st.get("n", 0) >= 8 and (st.get("ev_pct") or 0) > 0
                 and (st.get("first_year", {}).get("ev_pct") or -1) > 0
                 and (st.get("second_year", {}).get("ev_pct") or -1) > 0]
    port: Dict[str, Any] = {}
    for label, universe, maxpos in (
            ("base_2pos", BASE_UNIVERSE, 2),
            ("expanded_2pos", sorted(set(BASE_UNIVERSE + positives)), 2),
            ("expanded_3pos", sorted(set(BASE_UNIVERSE + positives)), 3)):
        pp = {**p, "universe": universe, "max_positions": maxpos}
        res = swing_engine.portfolio_backtest(daily, pp)
        port[label] = {**_metrics(res), "universe": universe, "max_positions": maxpos}
        print(f"  PORT {label:14s} {universe} ret {res['total_return_pct']}% mdd {res['mdd_pct']}% "
              f"wr {res['win_rate_pct']}% n {res['total_trades']} pf {res['profit_factor']}")

    hourly = _hourly_validation(daily, p, ["SOXL", "TQQQ", "SOXX", "AMZN", "GOOGL", "STX"], "--refresh" in args)
    for sym, st in hourly.items():
        if st.get("n"):
            print(f"  [60m] {sym:6s} n {st['n']:>2} ev {st['ev_pct']:>7}% wr {st['win_pct']:>5}% pf {st['profit_factor']} "
                  f"(일봉 {per_ticker.get(sym, {}).get('ev_pct')}%)")

    out = {
        "generated_at_kst": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
        "friction": "왕복 0.24% (NH 0.09% + 슬리피지 0.15%), SGOV 이자 4.52% 일할",
        "data": {s: {"bars": len(df), "from": str(df.index[0].date()), "to": str(df.index[-1].date())}
                 for s, df in daily.items()},
        "experiment_1_overlay": exp1,
        "experiment_1_recent_1y": exp1_recent,
        "experiment_2_per_ticker": per_ticker,
        "experiment_2_sensitivity": sens,
        "experiment_2_portfolio": port,
        "experiment_2_hourly_60m": hourly,
        "positive_candidates": positives,
        "note": ("시뮬레이션 전용 — 실전 원장과 분리. 일봉 체결 가정(다음 세션 시가, 손절·목표 동시 터치 시 손절 우선)."),
    }
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"saved {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
