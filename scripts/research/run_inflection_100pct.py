#!/usr/bin/env python3
"""변곡점 포착률 100% 극대화 연구 — 인과(Causal) vs 오라클(Oracle) 경계 규명 및
포착률 사다리(50% → 80% → 95~100%) 실측, 최적 스윗스팟 탐색.

동훈님 지시 (2026-11-18 11:38 KST): "변곡점 포착율을 100프로 까지 올릴 수 있는 방법을 연구해봐"

직전 연구(`intraday_inflection_research_20261118.md`)의 24개 주요 변곡점(±2.5%) ·
현행 엔진 포착률 1/24(4.2%)를 출발점으로 삼는다.

이 스크립트는 **연구 전용**이다.
  · `core/` · `config/` · 실전 매매 로직은 import 조차 하지 않는다(현행 엔진 재현은
    `i10_core_engine.py`가 읽기 전용 import로 수행).
  · git commit/push 없음. 산출물은 로컬 파일뿐.

------------------------------------------------------------------ 정의
변곡점 이벤트(정답 집합)
  · 리더(SOXL 1.5% / TQQQ 1.0%) 10분 종가 지그재그로 국소 고점·저점을 확정하고,
    |leg 이동| ≥ 2.5% 인 leg만 이벤트로 센다(직전 연구와 동일 규약·동일 24개 재현).
  · 방향 UP → 정방향(SOXL/TQQQ) 매수, DOWN → 인버스(SOXS/SQQQ) 매수로 집행 가능.
  · t0 = 시작 피벗 봉의 **종료**시각(결정 시각), t1 = 끝 피벗 봉의 종료시각.

포착(Recall)
  · 이벤트 e 를 "포착"했다 = e 와 같은 날·같은 종목에, e 의 [t0, t1] 구간 **안에서**
    진입한 거래가 하나 이상 있다. (직전 연구의 정의 그대로 — 비교 가능성 유지)

정밀도(Precision)
  · 전체 거래 중 위 조건으로 정답 이벤트에 걸린 거래의 비율. 나머지는 거짓신호(FP).

체결·비용
  · 신호는 10분봉 종가에서 계산하고 **그 봉 종가에 체결**(룩어헤드 없음).
  · 왕복 0.24%(편도 12bp), 정수주, 건당 예산 $150(실전 `entry_budget` 실효값과 동일 규모).
  · 당일 15:50 ET 강제청산(오버나이트 없음).

실행
  python3 scripts/research/run_inflection_100pct.py
출력
  data/research_inflection_100pct_20261118.json
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import i10_lib as L                       # noqa: E402
import i10_core_engine as CE              # noqa: E402

DATA = os.path.join(L.BASE_DIR, "data")
OUT = os.path.join(DATA, "research_inflection_100pct_20261118.json")
PRIOR = os.path.join(DATA, "research_10m_5d_20261118.json")

DAYS5 = list(L.SESSION_DAYS)
BUDGET = 150.0
RT = L.RT_COST                      # 왕복 0.24%
EOD_MIN = 15 * 60 + 50              # 15:50 ET 강제청산
BLACKOUT_END = 10 * 60              # 10:00 ET (개장 30분 유예)
NO_ENTRY_AFTER = 14 * 60 + 30       # 14:30 ET 신규 진입 마감

# pair = (leader, inverse, 지그재그 임계)
PAIRS: Dict[str, Tuple[str, str, float]] = {
    "SEMI": ("SOXL", "SOXS", 1.5),
    "NDX": ("TQQQ", "SQQQ", 1.0),
}
EVENT_MIN_MOVE = 2.5


# ============================================================== 1. 데이터 준비
def hm_min(hm: str) -> int:
    h, m = str(hm).split(":")
    return int(h) * 60 + int(m)


def prep_pair(panel: Dict[str, pd.DataFrame], days: List[str]) -> Dict[str, Dict[str, Any]]:
    """세션×페어 단위 배열 사전. 모든 지표는 10분봉 기준·해당 봉까지의 정보만."""
    out: Dict[str, Dict[str, Any]] = {}
    for pname, (lead, inv, _thr) in PAIRS.items():
        dl = panel[lead].copy()
        di = panel[inv].copy()
        dl["rsi2"] = L.rsi(dl["Close"], 2)
        dl["rsi14"] = L.rsi(dl["Close"], 14)
        _m, _u, _l, pctb = L.bollinger(dl["Close"], 20, 2.0)
        dl["pct_b"] = pctb
        dl["atr"] = L.atr(dl, 14)
        dl["atr_pct"] = dl["atr"] / dl["Close"] * 100
        dl["vol_ma20"] = dl["Volume"].rolling(20).mean().shift(1)
        dl["rvol"] = dl["Volume"] / dl["vol_ma20"].replace(0, np.nan)
        dl["ema9"] = L.ema(dl["Close"], 9)
        dl["ema21"] = L.ema(dl["Close"], 21)
        rows = []
        for day, g in dl.groupby("session"):
            g = g.copy()
            tp = (g["High"] + g["Low"] + g["Close"]) / 3.0
            v = g["Volume"].fillna(0).astype(float)
            g["vwap"] = (tp * v).cumsum() / v.cumsum().replace(0, np.nan)
            dev = g["Close"] - g["vwap"]
            g["vwap_sd"] = dev.expanding(min_periods=6).std()
            g["vwap_z"] = dev / g["vwap_sd"].replace(0, np.nan)
            rows.append(g)
        dl = pd.concat(rows).sort_index()
        di["atr"] = L.atr(di, 14)
        di["atr_pct"] = di["atr"] / di["Close"] * 100
        for day in days:
            g = dl[dl["session"] == day]
            gi = di[di["session"] == day]
            if len(g) < 5 or len(gi) < 5:
                continue
            out[f"{pname}|{day}"] = {
                "day": day,
                "hm": list(g["hm_end"]),
                "L_close": g["Close"].to_numpy(float), "L_open": g["Open"].to_numpy(float),
                "L_high": g["High"].to_numpy(float), "L_low": g["Low"].to_numpy(float),
                "rsi2": g["rsi2"].to_numpy(float), "rsi14": g["rsi14"].to_numpy(float),
                "pct_b": g["pct_b"].to_numpy(float), "rvol": g["rvol"].to_numpy(float),
                "atr_pct": g["atr_pct"].to_numpy(float), "vwap": g["vwap"].to_numpy(float),
                "vwap_z": g["vwap_z"].to_numpy(float), "ema9": g["ema9"].to_numpy(float),
                "ema21": g["ema21"].to_numpy(float),
                "I_close": gi["Close"].to_numpy(float), "I_open": gi["Open"].to_numpy(float),
                "I_high": gi["High"].to_numpy(float), "I_low": gi["Low"].to_numpy(float),
                "I_atr_pct": gi["atr_pct"].to_numpy(float),
            }
    return out


def daily_trend(panel: Dict[str, pd.DataFrame], days: List[str]) -> Dict[str, Dict[str, bool]]:
    """일봉 추세 필터 — 전일까지의 일봉 종가 vs EMA20 (룩어헤드 없음)."""
    out: Dict[str, Dict[str, bool]] = {}
    for pname, (lead, _inv, _thr) in PAIRS.items():
        b = panel[lead]
        d = b.groupby("session").agg({"Close": "last", "High": "max", "Low": "min"})
        ema20 = L.ema(d["Close"], 20)
        out[pname] = {}
        for i, day in enumerate(d.index):
            if i == 0:
                out[pname][day] = True
            else:
                out[pname][day] = bool(d["Close"].iloc[i - 1] >= ema20.iloc[i - 1])
    return out


# ============================================================== 2. 변곡점 이벤트
def zigzag(close: pd.Series, thr_pct: float) -> List[Dict[str, Any]]:
    """종가 지그재그 — thr% 이상 반전으로 국소 고점/저점 확정(직전 연구와 동일)."""
    c = list(map(float, close.values))
    pts: List[Dict[str, Any]] = []
    if len(c) < 3:
        return pts
    trend, ext, ext_i = 0, c[0], 0
    for i in range(1, len(c)):
        if trend >= 0:
            if c[i] > ext:
                ext, ext_i = c[i], i
            elif (ext - c[i]) / ext * 100.0 >= thr_pct:
                pts.append({"kind": "HIGH", "i": ext_i, "price": ext})
                trend, ext, ext_i = -1, c[i], i
        if trend <= 0:
            if c[i] < ext:
                ext, ext_i = c[i], i
            elif (c[i] - ext) / ext * 100.0 >= thr_pct:
                pts.append({"kind": "LOW", "i": ext_i, "price": ext})
                trend, ext, ext_i = 1, c[i], i
    return pts


def build_events(panel: Dict[str, pd.DataFrame], days: List[str]) -> List[Dict[str, Any]]:
    """±2.5% 주요 변곡점 이벤트 집합 (직전 연구의 중복제거 규약까지 동일)."""
    raw: List[Dict[str, Any]] = []
    for day in days:
        for pname, (lead, inv, thr) in PAIRS.items():
            g = panel[lead][panel[lead]["session"] == day]
            gi = panel[inv][panel[inv]["session"] == day]
            if len(g) < 3 or len(gi) < 3:
                continue
            pts = zigzag(g["Close"], thr)
            for a, b in zip(pts, pts[1:]):
                if a["kind"] == b["kind"] or b["i"] <= a["i"]:
                    continue
                mv = (b["price"] / a["price"] - 1) * 100
                if abs(mv) < EVENT_MIN_MOVE:
                    continue
                ins = lead if mv > 0 else inv
                src = g if ins == lead else gi
                px0, px1 = float(src["Close"].iloc[a["i"]]), float(src["Close"].iloc[b["i"]])
                raw.append({
                    "day": day, "pair": pname, "lead": lead, "instrument": ins,
                    "dir": "UP" if mv > 0 else "DOWN",
                    "t0": g["hm_end"].iloc[a["i"]], "t1": g["hm_end"].iloc[b["i"]],
                    "i0": int(a["i"]), "i1": int(b["i"]),
                    "move_lead_pct": round(mv, 2),
                    "instr_ret_pct": round((px1 / px0 - 1) * 100, 2),
                    "instr_px0": round(px0, 3), "instr_px1": round(px1, 3),
                    "under_px0": round(float(a["price"]), 3), "under_px1": round(float(b["price"]), 3),
                })
    # 직전 연구와 동일한 중복제거: (날짜, 피벗시각, 방향) — 인버스는 종목별로 분리
    seen, uniq = set(), []
    for r in sorted(raw, key=lambda x: -abs(x["move_lead_pct"])):
        key = (r["day"], r["t0"], r["dir"] if r["instrument"] in ("SOXL", "TQQQ")
               else r["instrument"] + r["dir"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return sorted(uniq, key=lambda x: (x["day"], x["t0"]))


# ============================================================== 3. 트리거(탐지기)
def _recent_extreme(s: Dict[str, Any], i: int, side: int, lookback: int = 3) -> int:
    """직전 lookback+1봉 안에서 반전의 근원이 된 극단 봉의 인덱스(룩어헤드 없음)."""
    lo = max(0, i - lookback)
    if side > 0:
        seg = s["L_low"][lo:i + 1]
        return lo + int(np.argmin(seg))
    seg = s["L_high"][lo:i + 1]
    return lo + int(np.argmax(seg))


def f_vol(s: Dict[str, Any], i: int, side: int) -> bool:
    """거래량 급증 — 극단 봉~트리거 봉 구간의 최대 RVOL ≥ 1.2."""
    p = _recent_extreme(s, i, side)
    seg = s["rvol"][p:i + 1]
    seg = seg[~np.isnan(seg)]
    return bool(len(seg)) and float(seg.max()) >= 1.2


def f_candle(s: Dict[str, Any], i: int, side: int) -> bool:
    """캐를 반전 꼬리 — 극단 봉 자체가 거부(rejection) 캔들이었는가."""
    p = _recent_extreme(s, i, side)
    o, c, h, l = s["L_open"][p], s["L_close"][p], s["L_high"][p], s["L_low"][p]
    rng = h - l
    if rng <= 0:
        return False
    if side > 0:
        return (min(o, c) - l) >= 0.35 * rng
    return (h - max(o, c)) >= 0.35 * rng


def f_rsi(s: Dict[str, Any], i: int, side: int) -> bool:
    """RSI 극단 — 반전의 근원 봉이 과매도/과매수였는가."""
    p = _recent_extreme(s, i, side)
    r = s["rsi2"][p]
    if np.isnan(r):
        return False
    return r <= 25.0 if side > 0 else r >= 75.0


def f_div(s: Dict[str, Any], i: int, side: int) -> bool:
    """RSI14 다이버전스 — 가격은 더 낮은데 모멘텀은 더 높다(2봉 비교)."""
    p = _recent_extreme(s, i, side)
    q = _recent_extreme(s, max(0, p - 1), side, lookback=4)
    if q >= p:
        return False
    dp = s["L_close"][p] - s["L_close"][q]
    dr = s["rsi14"][p] - s["rsi14"][q]
    if np.isnan(dr):
        return False
    return (dp < 0 and dr > 0) if side > 0 else (dp > 0 and dr < 0)


def f_mtf(s: Dict[str, Any], i: int, side: int) -> bool:
    """멀티타임프레임: 10분 EMA9/EMA21 추세 방향 일치."""
    a, b = s["ema9"][i], s["ema21"][i]
    if np.isnan(a) or np.isnan(b):
        return False
    return a > b if side > 0 else a < b


def f_daily(s: Dict[str, Any], i: int, side: int) -> bool:
    up = s.get("_daily_up", True)
    return up if side > 0 else (not up)


def f_vwap(s: Dict[str, Any], i: int, side: int) -> bool:
    v = s["vwap"][i]
    if np.isnan(v):
        return False
    return s["L_close"][i] <= v if side > 0 else s["L_close"][i] >= v


FILTERS: Dict[str, Callable[[Dict[str, Any], int, int], bool]] = {
    "vol": f_vol, "candle": f_candle, "rsi": f_rsi, "div": f_div,
    "mtf": f_mtf, "daily": f_daily, "vwap": f_vwap,
}


def _filters_ok(s: Dict[str, Any], i: int, side: int, names: Tuple[str, ...]) -> bool:
    return all(FILTERS[n](s, i, side) for n in names)


def trig_dip(s: Dict[str, Any], i: int, st: Dict[str, Any], p: Dict[str, Any]) -> int:
    """①-a 현행 DIP 계열(롱 전용) — RSI2/%B/RVOL 필터 완화 수준을 파라미터로."""
    if i < 1:
        return 0
    r, b, v = s["rsi2"][i], s["pct_b"][i], s["rvol"][i]
    if np.isnan(r) or np.isnan(b) or np.isnan(v):
        return 0
    if r <= p.get("rsi_max", 20.0) and b <= p.get("pctb_max", 0.15) and v >= p.get("rvol_min", 1.0):
        return 1
    return 0


def trig_cusum(s: Dict[str, Any], i: int, st: Dict[str, Any], p: Dict[str, Any]) -> int:
    """①-b CUSUM 변화점 + 반전 확인(c%). 하락 누적 sn≥h 후 상승 c 확인 → 롱."""
    c = s["L_close"][i]
    if i == 0:
        return 0
    r = (c / s["L_close"][i - 1] - 1) * 100
    k = float(p.get("k", 0.15))
    st["sp"] = max(0.0, st.get("sp", 0.0) + r - k)
    st["sn"] = max(0.0, st.get("sn", 0.0) - r - k)
    h = float(p.get("h", 2.0))
    conf = float(p.get("conf", 0.35))
    if st["sn"] >= h and st["sp"] >= conf:
        st["sp"] = 0.0
        return 1
    if st["sp"] >= h and st["sn"] >= conf:
        st["sn"] = 0.0
        return -1
    return 0


def trig_zscore(s: Dict[str, Any], i: int, st: Dict[str, Any], p: Dict[str, Any]) -> int:
    """①-c Z-score 동적 임계값 — VWAP 이탈 z 하향 후 종가 반전."""
    if i < 1:
        return 0
    z = s["vwap_z"][i]
    if np.isnan(z):
        return 0
    zt = float(p.get("z", 2.0))
    up = s["L_close"][i] > s["L_close"][i - 1]
    if z <= -zt and up:
        return 1
    if z >= zt and not up:
        return -1
    return 0


def trig_pivot(s: Dict[str, Any], i: int, st: Dict[str, Any], p: Dict[str, Any]) -> int:
    """④ 후행 피벗 확인(Trailing Pivot) — 진행 극단 대비 c% 반전에서 진입."""
    c = float(p.get("c", 1.5))
    cl = s["L_close"][i]
    _lo, _hi = st.get("lo"), st.get("hi")
    lo = cl if _lo is None else min(_lo, cl)
    hi = cl if _hi is None else max(_hi, cl)
    st["lo"], st["hi"] = lo, hi
    if cl >= lo * (1 + c / 100.0):
        st["lo"] = st["hi"] = cl
        return 1
    if cl <= hi * (1 - c / 100.0):
        st["lo"] = st["hi"] = cl
        return -1
    return 0


TRIGGERS = {"dip": trig_dip, "cusum": trig_cusum, "zscore": trig_zscore, "pivot": trig_pivot}


# ============================================================== 4. 시뮬레이터
def simulate(prep: Dict[str, Dict[str, Any]], dt: Dict[str, Dict[str, bool]], days: List[str],
             trigger: str, tparams: Dict[str, Any], exit_mode: str,
             filters: Tuple[str, ...] = (), blackout: bool = True, vix_gate: bool = False,
             budget: float = BUDGET, rt: float = RT) -> List[Dict[str, Any]]:
    """한 탐지기 설정을 모든 세션에 대해 실행. 페어별 1포지션(동시 2포지션까지)."""
    trig = TRIGGERS[trigger]
    trades: List[Dict[str, Any]] = []
    for day in days:
        for pname, (lead, inv, _thr) in PAIRS.items():
            s = prep.get(f"{pname}|{day}")
            if not s:
                continue
            s = dict(s)
            s["_daily_up"] = dt[pname].get(day, True)
            pos: Optional[Dict[str, Any]] = None
            st: Dict[str, Any] = {}
            hm = s["hm"]
            for i in range(len(hm)):
                dec = hm_min(hm[i])
                # ---------------- 청산 판정 (같은 봉 종가)
                if pos is not None:
                    key = "L_close" if pos["ticker"] == lead else "I_close"
                    px = s[key][i]
                    pos["peak"] = max(pos["peak"], px) if pos["dir"] > 0 else min(pos["peak"], px)
                    act = None
                    if dec >= EOD_MIN:
                        act = ("EOD", px)
                    elif pos["dir"] > 0 and px <= pos["stop"]:
                        act = ("STOP", px)
                    elif pos["dir"] < 0 and px >= pos["stop"]:
                        act = ("STOP", px)
                    else:
                        atr_abs = pos["entry"] * pos["atr_pct"] / 100.0
                        if exit_mode == "trail":
                            if pos["dir"] > 0 and px <= pos["peak"] - pos["trail_mult"] * atr_abs:
                                act = ("TRAIL", px)
                            if pos["dir"] < 0 and px >= pos["peak"] + pos["trail_mult"] * atr_abs:
                                act = ("TRAIL", px)
                        elif exit_mode == "flip":
                            cc = pos["c_exit"]
                            if pos["dir"] > 0 and px <= pos["peak"] * (1 - cc / 100.0):
                                act = ("FLIP", px)
                            if pos["dir"] < 0 and px >= pos["peak"] * (1 + cc / 100.0):
                                act = ("FLIP", px)
                    if act:
                        reason, pxo = act
                        trades.append(_trade(day, hm[i], pname, pos["ticker"], pos["dir"], pos["entry_hm"],
                                             pos["entry"], pxo, pos["qty"], reason, rt))
                        pos = None
                        st["lo"] = st["hi"] = None
                # ---------------- 진입 판정
                if pos is not None:
                    continue
                if blackout and dec <= BLACKOUT_END:
                    continue
                if dec > NO_ENTRY_AFTER:
                    continue
                side = trig(s, i, st, tparams)
                if not side:
                    continue
                if filters and not _filters_ok(s, i, side, filters):
                    continue
                ticker = lead if side > 0 else inv
                px = s["L_close"][i] if side > 0 else s["I_close"][i]
                atr_pct = (s["atr_pct"][i] if side > 0 else s["I_atr_pct"][i])
                if np.isnan(atr_pct) or atr_pct <= 0:
                    atr_pct = 2.0
                qty = int(budget // px) if px > 0 else 0
                if qty <= 0:
                    continue
                sl_pct = max(1.5, min(15.0, 0.75 * float(atr_pct)))
                pos = {
                    "dir": side, "ticker": ticker, "entry": float(px), "qty": qty,
                    "entry_hm": hm[i], "peak": float(px), "atr_pct": float(atr_pct),
                    "trail_mult": 0.9,
                    "stop": float(px) * (1 - sl_pct / 100.0) if side > 0 else float(px) * (1 + sl_pct / 100.0),
                    "c_exit": float(tparams.get("c_exit", tparams.get("c", 1.5))),
                }
                st["lo"] = st["hi"] = None
    return trades


def _trade(day: str, hm: str, pair: str, ticker: str, side: int, entry_hm: str,
           entry: float, exit_px: float, qty: int, reason: str, rt: float) -> Dict[str, Any]:
    gross = (exit_px / entry - 1) * 100
    notional = entry * qty
    pnl = (exit_px - entry) * qty - notional * rt
    return {"day": day, "hm": hm, "entry_hm": entry_hm, "pair": pair, "ticker": ticker,
            "side": "LONG" if side > 0 else "SHORT", "entry": round(entry, 3),
            "exit": round(exit_px, 3), "qty": qty, "notional_usd": round(notional, 2),
            "gross_pct": round(gross, 3), "ret_pct": round(pnl / notional * 100, 3) if notional else 0.0,
            "pnl_usd": round(pnl, 2), "fee_usd": round(notional * rt, 2),
            "hold_min": hm_min(hm) - hm_min(entry_hm), "reason": reason}


# ============================================================== 5. 채점
def score(trades: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recall(이벤트 포착률) · Precision(신호 정밀도) · leg 캡처율."""
    if not events:
        return {}
    matched: Dict[int, Dict[str, Any]] = {}
    tp = 0
    for t in trades:
        hit = None
        for k, e in enumerate(events):
            if e["day"] == t["day"] and e["instrument"] == t["ticker"] and e["t0"] <= t["entry_hm"] <= e["t1"]:
                hit = k
                break
        if hit is not None:
            tp += 1
            matched.setdefault(hit, t)
    n_ev = len(events)
    n_tr = len(trades)
    cap_frac = []
    for k, t in matched.items():
        e = events[k]
        cap_frac.append(t["gross_pct"] / e["instr_ret_pct"] if e["instr_ret_pct"] else 0.0)
    return {"n_events": n_ev, "n_captured": len(matched),
            "recall_pct": round(100.0 * len(matched) / n_ev, 1),
            "n_trades": n_tr, "n_true": tp, "n_false": n_tr - tp,
            "precision_pct": round(100.0 * tp / n_tr, 1) if n_tr else None,
            "capture_ratio_median": round(float(np.median(cap_frac)), 3) if cap_frac else None,
            "captured_leg_sum_pct": round(sum(events[k]["instr_ret_pct"] for k in matched), 1),
            "captured_leg_usd": round(sum(abs(events[k]["instr_ret_pct"]) for k in matched) * BUDGET / 100, 2)}


def perf(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0, "pnl_usd": 0.0, "win_pct": None, "pf": None, "mdd_usd": 0.0,
                "fee_usd": 0.0, "avg_pct": None, "t_stat": None, "days": 0}
    r = np.array([t["ret_pct"] for t in trades], float)
    pnl = np.array([t["pnl_usd"] for t in trades], float)
    wins, losses = r[r > 0], r[r <= 0]
    gw, gl = float(wins.sum()), float(abs(losses.sum()))
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    gross_abs = sum(abs(t["gross_pct"]) / 100.0 * t["notional_usd"] for t in trades)
    fee = sum(t["fee_usd"] for t in trades)
    per_day: Dict[str, float] = {}
    for t in trades:
        per_day[t["day"]] = per_day.get(t["day"], 0.0) + t["pnl_usd"]
    tstat = float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 2 and r.std(ddof=1) > 0 else None
    return {"n": len(trades), "pnl_usd": round(float(pnl.sum()), 2),
            "win_pct": round(100.0 * len(wins) / len(trades), 1),
            "pf": round(gw / gl, 2) if gl else None,
            "mdd_usd": round(float((eq - peak).min()), 2),
            "fee_usd": round(fee, 2),
            "fee_share_pct": round(100 * fee / gross_abs, 1) if gross_abs else None,
            "avg_pct": round(float(r.mean()), 3), "t_stat": round(tstat, 2) if tstat is not None else None,
            "days": len(per_day), "pos_days": sum(1 for v in per_day.values() if v > 0),
            "per_day": {k: round(v, 2) for k, v in sorted(per_day.items())},
            "per_day_avg": round(float(pnl.sum()) / max(1, len(per_day)), 2)}


# ============================================================== 6. 이론부 실측
def delay_analysis(panel: Dict[str, pd.DataFrame], events: List[Dict[str, Any]],
                   ks: Tuple[int, ...] = (0, 1, 2, 3, 4, 5)) -> Dict[str, Any]:
    """오라클(포착률 100%)에서 확인 지연 k봉의 가격 이탈분과 잔여수익."""
    out = {}
    for k in ks:
        rows = []
        for e in events:
            src = panel[e["instrument"]]
            g = src[src["session"] == e["day"]]
            j = e["i0"] + k
            if j > e["i1"] or j >= len(g):
                rows.append({"day": e["day"], "instrument": e["instrument"], "valid": False})
                continue
            px_in = float(g["Close"].iloc[j])
            px_out = float(g["Close"].iloc[e["i1"]])
            ret = (px_out / px_in - 1) * 100 - RT * 100
            depart = (px_in / e["instr_px0"] - 1) * 100
            rows.append({"day": e["day"], "instrument": e["instrument"], "valid": True,
                         "departure_pct": round(depart, 3), "ret_pct": round(ret, 3),
                         "usd": round(ret / 100 * BUDGET, 2),
                         "remaining_frac": round(ret / (e["instr_ret_pct"] - RT * 100), 3)
                         if e["instr_ret_pct"] else None})
        ok = [r for r in rows if r.get("valid")]
        out[f"delay_{k}"] = {
            "n": len(ok), "usd_total": round(sum(r["usd"] for r in ok), 2),
            "mean_departure_pct": round(float(np.mean([abs(r["departure_pct"]) for r in ok])), 3),
            "mean_ret_pct": round(float(np.mean([r["ret_pct"] for r in ok])), 3),
            "neg_ret_share_pct": round(100.0 * sum(1 for r in ok if r["ret_pct"] < 0) / len(ok), 1),
            "mean_remaining_frac": round(float(np.mean([r["remaining_frac"] for r in ok
                                                        if r["remaining_frac"] is not None])), 3),
        }
    return out


def trigger_quality(prep: Dict[str, Dict[str, Any]], days: List[str], trigger: str,
                    tparams: Dict[str, Any], horizon: int = 6) -> Dict[str, Any]:
    """트리거 이후 MFE/MAE 분포 — 진입 후 최대 유리/불리 이동(거래비용 전)."""
    trig = TRIGGERS[trigger]
    mfe, mae = [], []
    for day in days:
        for pname, (lead, inv, _thr) in PAIRS.items():
            s = prep.get(f"{pname}|{day}")
            if not s:
                continue
            st: Dict[str, Any] = {}
            hm = s["hm"]
            for i in range(len(hm)):
                side = trig(s, i, st, tparams)
                if not side:
                    continue
                key = "L_close" if side > 0 else "I_close"
                px = s[key][i]
                fut = s[key][i + 1:i + 1 + horizon]
                if not len(fut):
                    continue
                if side > 0:
                    mfe.append((fut.max() / px - 1) * 100)
                    mae.append((fut.min() / px - 1) * 100)
                else:
                    mfe.append((px / fut.min() - 1) * 100)
                    mae.append((px / fut.max() - 1) * 100)
    if not mfe:
        return {}
    mfe_a, mae_a = np.array(mfe), np.array(mae)
    return {"n": len(mfe), "mfe_mean_pct": round(float(mfe_a.mean()), 2),
            "mae_mean_pct": round(float(mae_a.mean()), 2),
            "mfe_med_pct": round(float(np.median(mfe_a)), 2),
            "mae_med_pct": round(float(np.median(mae_a)), 2),
            "p_mfe_ge_cost_pct": round(100.0 * float((mfe_a >= RT * 100 + 0.1).mean()), 1),
            "p_mae_le_1pct_pct": round(100.0 * float((mae_a <= -1.0).mean()), 1),
            "mfe_over_mae": round(float(mfe_a.mean() / abs(mae_a.mean())), 2) if mae_a.mean() else None,
            "ratio_pct": round(100.0 * float(mfe_a.mean()) / abs(float(mae_a.mean())), 1) if mae_a.mean() else None}


# ============================================================== 7. 엔진(L0)
def run_engine(panel, panel5, days, vix10, dxy10, sentiment, policy, params) -> List[Dict[str, Any]]:
    daily = CE.daily_bars(sorted({p for _l, _i, _t in PAIRS.values() for p in (_l, _i)}))
    dm = {k: CE.daily_metrics(v) for k, v in daily.items()}
    sig: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for d in days:
        sig[d] = {}
        dday = pd.Timestamp(d)
        for tk in ("SOXL", "TQQQ"):
            tab = dm.get(tk)
            if tab is None:
                continue
            prev = tab[tab.index < dday]
            if not len(prev):
                continue
            row = prev.iloc[-1]
            ok = (float(row["rsi2"]) <= 20.0 and float(row["pct_b"]) <= 0.15 and float(row["rvol"]) >= 1.0)
            sig[d][tk] = {"as_of": str(prev.index[-1])[:10], "rsi2": float(row["rsi2"]),
                          "pct_b": float(row["pct_b"]), "rvol": float(row["rvol"]),
                          "atr_pct": float(row["atr_pct"]), "signal": bool(ok)}
    res = CE.sim_core(days, panel, panel5, sig, vix10=vix10, dxy10=dxy10, sentiment=sentiment,
                      budget_mode="current", policy=policy, params=params, label="engine")
    out = []
    for t in res["trades"]:
        if "OPEN_AT_WINDOW_END" in t["reason"]:
            continue
        out.append({"day": t["date"], "hm": str(pd.Timestamp(t["exit_ts"]))[11:16],
                    "entry_hm": str(pd.Timestamp(t["entry_ts"]))[11:16], "pair": "SEMI" if t["ticker"] in ("SOXL", "SOXS") else "NDX",
                    "ticker": t["ticker"], "side": "LONG", "entry": t["entry"], "exit": t["exit"],
                    "qty": t["qty"], "notional_usd": t["notional_usd"], "gross_pct": t["gross_pct"],
                    "ret_pct": t["ret_pct"], "pnl_usd": t["pnl_usd"], "fee_usd": t["fee_usd"],
                    "hold_min": t["hold_min"], "reason": t["reason"]})
    return out


# ============================================================== 8. 사다리 정의
def ladder_configs() -> List[Dict[str, Any]]:
    """포착률을 단계적으로 끌어올리는 탐지기 스펙트럼.

    L0 현행 엔진 (기준선)
    L1 필터 완화 (롱 전용)                        — 현행 DIP의 RSI2/%B/RVOL 문턱만 완화
    L2 CUSUM 변화점 + 양방향 + 개장 블랙아웃 해제   — ①
    L3 L2 + 거래량 급증 + 캔들 반전 꼬리           — ①+②
    L4 L3 + RSI 극단/다이버전스 + MTF 추세 필터    — ①+②+④
    L5 후행 피벗 확인(c=지그재그 임계) + 거래량     — ④ (포착률 상한 근처)
    L6 후행 피벗 확인(c=지그재그 임계) 무필터        — 대조군(최대 포착률·최저 정밀도)
    """
    return [
        {"id": "L0", "name": "현행 엔진", "kind": "engine"},
        {"id": "L1", "name": "필터 완화(롱 전용)", "kind": "sim", "trigger": "dip",
         "params": {"rsi_max": 35.0, "pctb_max": 0.5, "rvol_min": 0.5},
         "filters": (), "blackout": True},
        {"id": "L2", "name": "CUSUM 변화점+양방향+블랙아웃 해제", "kind": "sim", "trigger": "cusum",
         "params": {"h": 2.0, "k": 0.15, "conf": 0.35}, "filters": (), "blackout": False},
        {"id": "L3", "name": "L2+거래량급증+캔들반전", "kind": "sim", "trigger": "cusum",
         "params": {"h": 1.2, "k": 0.15, "conf": 0.35}, "filters": ("vol", "candle"), "blackout": False},
        {"id": "L4", "name": "L3+RSI극단+MTF추세", "kind": "sim", "trigger": "cusum",
         "params": {"h": 0.8, "k": 0.15, "conf": 0.30}, "filters": ("vol", "candle", "rsi", "mtf"),
         "blackout": False},
        {"id": "L5", "name": "후행피벗(c=임계)+거래량", "kind": "sim", "trigger": "pivot",
         "params": {"c": 1.2, "c_exit": 1.2}, "filters": ("vol",), "blackout": False},
        {"id": "L6", "name": "후행피벗(c=임계) 무필터", "kind": "sim", "trigger": "pivot",
         "params": {"c": 1.2, "c_exit": 1.2}, "filters": (), "blackout": False},
    ]


def c_sweep() -> List[Dict[str, Any]]:
    """주 파라미터 c(%) 격자 — 포착률-순수익 곡선의 원자료."""
    out = []
    for c in (2.5, 2.0, 1.5, 1.2, 1.0, 0.75, 0.5, 0.35):
        for flt, fname in (((), "무필터"), (("vol",), "+거래량"), (("vol", "mtf"), "+거래량+MTF"),
                           (("vol", "candle", "rsi", "mtf"), "+4필터")):
            out.append({"id": f"c{c}_{fname}", "c": c, "filters": flt, "name": fname})
    return out


# ============================================================== 9. main
def main() -> int:
    panel = L.panel(period="60d")
    panel5 = {s: CE.five_min_metrics(L.load5m(s, "60d")) for s in L.SYMBOLS}
    vix10 = panel["^VIX"]["Close"]
    dxy10 = L.to10m(L.load5m("DX-Y.NYB", "60d"))["Close"]
    sentiment = None
    try:
        sentiment = float(json.load(open(os.path.join(DATA, "news_sentiment_report.json")))
                          .get("average_sentiment_score"))
    except Exception:
        pass
    from core import execution_policy
    policy = execution_policy.load_policy()
    params = json.load(open(os.path.join(DATA, "strategy_params.json")))

    all_days = sorted(panel["SOXL"]["session"].unique())
    prep_all = prep_pair(panel, all_days)
    dt_all = daily_trend(panel, all_days)

    ev_all = build_events(panel, all_days)
    ev_5d = [e for e in ev_all if e["day"] in DAYS5]

    # 직전 연구 24개와 일치 검증
    prior = json.load(open(PRIOR))["capture"]
    prior_keys = sorted((d, r["instrument"], r["from_hm"], r["to_hm"]) for d, rs in prior.items() for r in rs)
    mine_keys = sorted((e["day"], e["instrument"], e["t0"], e["t1"]) for e in ev_5d)
    event_set_match = prior_keys == mine_keys

    result: Dict[str, Any] = {
        "generated_at_kst": pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S"),
        "window": {"5d": DAYS5, "60d": [all_days[0], all_days[-1], len(all_days)]},
        "cost_round_trip_pct": RT * 100, "budget_usd": BUDGET,
        "event_set": {"n_5d": len(ev_5d), "n_60d": len(ev_all),
                      "matches_prior_24": bool(event_set_match),
                      "prior_keys_not_matched": [k for k in prior_keys if k not in mine_keys][:5],
                      "sum_abs_instr_ret_5d": round(sum(abs(e["instr_ret_pct"]) for e in ev_5d), 1),
                      "sum_abs_instr_ret_60d": round(sum(abs(e["instr_ret_pct"]) for e in ev_all), 1),
                      "median_abs_instr_ret_60d": round(float(np.median([abs(e["instr_ret_pct"]) for e in ev_all])), 2),
                      "per_day_60d": round(len(ev_all) / len(all_days), 2)},
        "events_5d": ev_5d,
        "events_60d": ev_all,
    }

    # ---------------------------------------------------- 이론: 오라클·지연
    result["theory"] = {
        "oracle_5d": delay_analysis(panel, ev_5d, ks=(0, 1, 2, 3, 4, 5)),
        "oracle_60d": delay_analysis(panel, ev_all, ks=(0, 1, 2, 3, 4, 5)),
        "trigger_quality_60d": {
            f"c{c}": trigger_quality(prep_all, all_days, "pivot", {"c": c})
            for c in (2.5, 2.0, 1.5, 1.2, 1.0, 0.75, 0.5, 0.35)
        },
    }

    # ---------------------------------------------------- 사다리
    ladder_rows = []
    for cfg in ladder_configs():
        for exit_mode in ("trail", "flip"):
            if cfg["kind"] == "engine":
                if exit_mode == "flip":
                    continue
                tr_all = run_engine(panel, panel5, all_days, vix10, dxy10, sentiment, policy, params)
                tr_5d = [t for t in tr_all if t["day"] in DAYS5]
            else:
                tr_all = simulate(prep_all, dt_all, all_days, cfg["trigger"], cfg["params"], exit_mode,
                                  filters=cfg["filters"], blackout=cfg["blackout"])
                tr_5d = [t for t in tr_all if t["day"] in DAYS5]
            s5 = score(tr_5d, ev_5d)
            s60 = score(tr_all, ev_all)
            ladder_rows.append({
                "id": cfg["id"], "name": cfg["name"], "exit": exit_mode,
                "params": cfg.get("params", {}), "filters": list(cfg.get("filters", ())),
                "blackout": cfg.get("blackout", True),
                "5d": {**perf(tr_5d), **{f"score_{k}": v for k, v in s5.items()}},
                "60d": {**perf(tr_all), **{f"score_{k}": v for k, v in s60.items()}},
            })
            print(f"{cfg['id']}/{exit_mode}: 5d n={len(tr_5d)} pnl={perf(tr_5d)['pnl_usd']} "
                  f"recall={s5.get('recall_pct')} prec={s5.get('precision_pct')} | "
                  f"60d n={len(tr_all)} pnl={perf(tr_all)['pnl_usd']} recall={s60.get('recall_pct')} "
                  f"prec={s60.get('precision_pct')}")
    result["ladder"] = ladder_rows

    # ---------------------------------------------------- c 격자
    grid_rows = []
    for g in c_sweep():
        for exit_mode in ("trail", "flip"):
            tp = {"c": g["c"], "c_exit": g["c"]}
            tr_all = simulate(prep_all, dt_all, all_days, "pivot", tp, exit_mode,
                              filters=g["filters"], blackout=False)
            tr_5d = [t for t in tr_all if t["day"] in DAYS5]
            s5, s60 = score(tr_5d, ev_5d), score(tr_all, ev_all)
            grid_rows.append({"c": g["c"], "filters": list(g["filters"]), "filter_name": g["name"],
                              "exit": exit_mode,
                              "5d": {**perf(tr_5d), **{f"score_{k}": v for k, v in s5.items()}},
                              "60d": {**perf(tr_all), **{f"score_{k}": v for k, v in s60.items()}}})
            print(f"c={g['c']} {g['name']}/{exit_mode}: 5d n={len(tr_5d)} pnl={perf(tr_5d)['pnl_usd']} "
                  f"recall={s5.get('recall_pct')} | 60d n={len(tr_all)} pnl={perf(tr_all)['pnl_usd']} "
                  f"recall={s60.get('recall_pct')} prec={s60.get('precision_pct')}")
    result["grid"] = grid_rows

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print("saved", OUT)
    print("event set matches prior 24:", event_set_match)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
