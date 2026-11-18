#!/usr/bin/env python3
"""변곡점 포착률 100% 극대화 연구 — 인과(Causal) vs 오라클(Oracle) 경계 규명,
포착률 사다리(4% → 50% → 80% → 95~100%) 실측, 최적 스윗스팟 탐색.

동훈님 지시 (2026-11-18 11:38 KST): "변곡점 포착율을 100프로 까지 올릴 수 있는 방법을 연구해봐"

직전 연구(`intraday_inflection_research_20261118.md`)의 24개 주요 변곡점(±2.5%) ·
현행 엔진 포착률 1/24(4.2%)를 출발점으로 삼는다.

이 스크립트는 **연구 전용**이다.
  · `core/` · `config/` · 실전 매매 로직은 한 줄도 바꾸지 않는다(현행 엔진 재현은
    `i10_core_engine.py`가 읽기 전용 import로 수행).
  · git commit/push 없음. 산출물은 로컬 파일뿐.

------------------------------------------------------------------ 정의
변곡점 이벤트(정답 집합)
  · 리더(SOXL 1.5% / TQQQ 1.0%) 10분 종가 지그재그로 국소 고점·저점을 확정하고,
    |leg 이동| ≥ 2.5% 인 leg만 이벤트로 센다(직전 연구와 동일 규약 — 24개 재현 검증).
  · UP → 정방향(SOXL/TQQQ) 매수, DOWN → 인버스(SOXS/SQQQ) 매수로 집행 가능.
  · t0 = 시작 피벗 봉의 **종료**시각(결정 시각), t1 = 끝 피벗 봉의 종료시각.

포착(Recall)
  · 이벤트 e 를 "포착" = e 와 같은 날·같은 종목에, e 의 [t0, t1] 구간 **안에서** 진입한
    거래가 하나 이상 있다(직전 연구의 정의 그대로 — 비교 가능성 유지).

정밀도(Precision)
  · 전체 거래 중 위 조건으로 정답 이벤트에 걸린 거래의 비율. 나머지는 거짓신호(FP).

체결·비용
  · 신호는 10분봉 종가에서 계산하고 **그 봉 종가에 체결**(룩어헤드 없음).
  · 왕복 0.24%(편도 12bp), 정수주, 건당 예산 $150.
  · 당일 15:50 ET 강제청산(오버나이트 없음). 손절 = clamp(0.75×ATR%, 1.5%, 15%).

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

# pair = (리더, 인버스, 지그재그 임계)
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
        for _day, g in dl.groupby("session"):
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
                "day": day, "pair": pname, "hm": list(g["hm_end"]),
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


def prep_5m(days: List[str]) -> Dict[str, Dict[str, Any]]:
    """5분봉 판정 격자(지시 ④의 5m 미세구조 확인). 지표·체결 모두 5분봉 종가."""
    out: Dict[str, Dict[str, Any]] = {}
    cache: Dict[str, pd.DataFrame] = {}
    for pname, (lead, inv, _thr) in PAIRS.items():
        for sym in (lead, inv):
            if sym in cache:
                continue
            d = L.regular_session(L.load5m(sym, "60d")).copy()
            d["rsi2"] = L.rsi(d["Close"], 2)
            _m, _u, _l, pctb = L.bollinger(d["Close"], 20, 2.0)
            d["pct_b"] = pctb
            d["atr_pct"] = L.atr(d, 14) / d["Close"] * 100
            d["vol_ma20"] = d["Volume"].rolling(20).mean().shift(1)
            d["rvol"] = d["Volume"] / d["vol_ma20"].replace(0, np.nan)
            d["ema9"] = L.ema(d["Close"], 9)
            d["ema21"] = L.ema(d["Close"], 21)
            d["session5"] = [str(x.date()) for x in d.index]
            rows = []
            for _d, g in d.groupby("session5"):
                g = g.copy()
                tp = (g["High"] + g["Low"] + g["Close"]) / 3.0
                v = g["Volume"].fillna(0).astype(float)
                g["vwap"] = (tp * v).cumsum() / v.cumsum().replace(0, np.nan)
                dev = g["Close"] - g["vwap"]
                g["vwap_sd"] = dev.expanding(min_periods=6).std()
                g["vwap_z"] = dev / g["vwap_sd"].replace(0, np.nan)
                rows.append(g)
            cache[sym] = pd.concat(rows).sort_index()
        for day in days:
            g = cache[lead][cache[lead]["session5"] == day]
            gi = cache[inv][cache[inv]["session5"] == day]
            if len(g) < 10 or len(gi) < 10:
                continue
            out[f"{pname}|{day}"] = {
                "day": day, "pair": pname,
                "hm": [(ts + pd.Timedelta(minutes=5)).strftime("%H:%M") for ts in g.index],
                "L_close": g["Close"].to_numpy(float), "L_open": g["Open"].to_numpy(float),
                "L_high": g["High"].to_numpy(float), "L_low": g["Low"].to_numpy(float),
                "rsi2": g["rsi2"].to_numpy(float), "rsi14": g["rsi2"].to_numpy(float),
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
        d = panel[lead].groupby("session").agg({"Close": "last"})
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
    seen, uniq = set(), []
    for r in sorted(raw, key=lambda x: -abs(x["move_lead_pct"])):
        key = (r["day"], r["t0"], r["dir"] if r["instrument"] in ("SOXL", "TQQQ")
               else r["instrument"] + r["dir"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return sorted(uniq, key=lambda x: (x["day"], x["t0"]))


# ============================================================== 3. 필터
def _recent_extreme(s: Dict[str, Any], i: int, side: int, lookback: int = 3) -> int:
    """직전 lookback봉 안에서 반전의 근원이 된 극단 봉의 인덱스(룩어헤드 없음)."""
    lo = max(0, i - lookback)
    if side > 0:
        seg = s["L_low"][lo:i + 1]
        return lo + int(np.argmin(seg))
    seg = s["L_high"][lo:i + 1]
    return lo + int(np.argmax(seg))


def f_vol(s: Dict[str, Any], i: int, side: int) -> bool:
    """거래량 급증 — 극단 봉~트리거 봉 구간의 최대 RVOL ≥ 1.2 (지시 ①)."""
    p = _recent_extreme(s, i, side)
    seg = s["rvol"][p:i + 1]
    seg = seg[~np.isnan(seg)]
    return bool(len(seg)) and float(seg.max()) >= 1.2


def f_candle(s: Dict[str, Any], i: int, side: int) -> bool:
    """캔들 반전 꼬리 — 극단 봉 자체가 거부(rejection) 캔들이었는가 (지시 ②)."""
    p = _recent_extreme(s, i, side)
    o, c, h, l = s["L_open"][p], s["L_close"][p], s["L_high"][p], s["L_low"][p]
    rng = h - l
    if rng <= 0:
        return False
    if side > 0:
        return (min(o, c) - l) >= 0.35 * rng
    return (h - max(o, c)) >= 0.35 * rng


def f_rsi(s: Dict[str, Any], i: int, side: int) -> bool:
    """RSI 극단 — 반전의 근원 봉이 과매도/과매수였는가 (지시 ②)."""
    p = _recent_extreme(s, i, side)
    r = s["rsi2"][p]
    if np.isnan(r):
        return False
    return r <= 25.0 if side > 0 else r >= 75.0


def f_div(s: Dict[str, Any], i: int, side: int) -> bool:
    """RSI14 다이버전스 — 가격은 더 낮은데 모멘텀은 더 높다 (지시 ②)."""
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
    """멀티타임프레임 — 10분 EMA9/EMA21 추세 방향 일치 (지시 ④)."""
    a, b = s["ema9"][i], s["ema21"][i]
    if np.isnan(a) or np.isnan(b):
        return False
    return a > b if side > 0 else a < b


def f_daily(s: Dict[str, Any], i: int, side: int) -> bool:
    up = bool(s.get("_daily_up", True))
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


# ============================================================== 4. 트리거(탐지기)
def trig_dip(s: Dict[str, Any], i: int, st: Dict[str, Any], p: Dict[str, Any]) -> int:
    """ⓐ 현행 DIP 계열(롱 전용) — RSI2/%B/RVOL 문턱을 파라미터로."""
    if i < 1:
        return 0
    r, b, v = s["rsi2"][i], s["pct_b"][i], s["rvol"][i]
    if np.isnan(r) or np.isnan(b) or np.isnan(v):
        return 0
    if r <= p.get("rsi_max", 20.0) and b <= p.get("pctb_max", 0.15) and v >= p.get("rvol_min", 1.0):
        return 1
    return 0


def trig_cusum(s: Dict[str, Any], i: int, st: Dict[str, Any], p: Dict[str, Any]) -> int:
    """① CUSUM 변화점 — 하락 누적 sn ≥ h 후 반전 c 확인 → 롱 (반대는 숏)."""
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
    """① Z-score 동적 임계값 — VWAP 이탈 z 후 종가 반전."""
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
    """④ 후행 피벗 확인 — 진행 극단 대비 c% 반전에서 진입(양방향)."""
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


TRIGGERS: Dict[str, Callable[..., int]] = {
    "dip": trig_dip, "cusum": trig_cusum, "zscore": trig_zscore, "pivot": trig_pivot,
}


# ============================================================== 5. 시뮬레이터
def simulate(prep: Dict[str, Dict[str, Any]], dt: Dict[str, Dict[str, bool]], days: List[str],
             trigger: str, tparams: Dict[str, Any], exit_mode: str,
             filters: Tuple[str, ...] = (), blackout: bool = True,
             budget: float = BUDGET, rt: float = RT, diag: Optional[Dict[str, Any]] = None,
             log: Optional[List[Dict[str, Any]]] = None, trail_mult: float = 1.5
             ) -> List[Dict[str, Any]]:
    """한 탐지기 설정을 모든 세션에 대해 실행.

    · **신호 흐름은 포지션과 독립**이다. 트리거는 매 봉 평가되어 진행 극단을 갱신한다
      (보유 중·개장 유예 중에도 평가) — 실전 엔진이 10분마다 깨어나 지표를 읽는 순서와 같다.
    · 페어별 1포지션(최대 2포지션 동시 보유). 보유 중에는 신규 진입을 받지 않는다
      (=한 슬롯을 회전시키는 구조라 "보유 점유율"이 포착률의 물리적 상한이 된다).
    """
    trig = TRIGGERS[trigger]
    trades: List[Dict[str, Any]] = []
    diag = diag if diag is not None else {}
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
                            if pos["dir"] < 0 and px >= pos["peak"] * (1 - cc / 100.0):
                                act = ("FLIP", px)
                    if act:
                        reason, pxo = act
                        trades.append(_trade(day, hm[i], pname, pos["ticker"], pos["dir"],
                                             pos["entry_hm"], pos["entry"], pxo, pos["qty"],
                                             reason, rt))
                        pos = None
                # ---------------- 트리거 신호 (포지션과 무관하게 매 봉 평가)
                side = trig(s, i, st, tparams)
                if not side:
                    continue
                # ---------------- 진입 집행
                if pos is not None:
                    diag["skip_in_position"] = diag.get("skip_in_position", 0) + 1
                    if log is not None:
                        log.append({"day": day, "pair": pname, "hm": hm[i], "side": side,
                                    "outcome": "skip_in_position"})
                    continue
                if blackout and dec <= BLACKOUT_END:
                    diag["skip_blackout"] = diag.get("skip_blackout", 0) + 1
                    if log is not None:
                        log.append({"day": day, "pair": pname, "hm": hm[i], "side": side,
                                    "outcome": "skip_blackout"})
                    continue
                if dec > NO_ENTRY_AFTER:
                    diag["skip_late"] = diag.get("skip_late", 0) + 1
                    if log is not None:
                        log.append({"day": day, "pair": pname, "hm": hm[i], "side": side,
                                    "outcome": "skip_late"})
                    continue
                if filters and not _filters_ok(s, i, side, filters):
                    diag["skip_filter"] = diag.get("skip_filter", 0) + 1
                    if log is not None:
                        log.append({"day": day, "pair": pname, "hm": hm[i], "side": side,
                                    "outcome": "skip_filter"})
                    continue
                ticker = lead if side > 0 else inv
                px = s["L_close"][i] if side > 0 else s["I_close"][i]
                atr_pct = (s["atr_pct"][i] if side > 0 else s["I_atr_pct"][i])
                if np.isnan(atr_pct) or atr_pct <= 0:
                    atr_pct = 2.0
                qty = int(budget // px) if px > 0 else 0
                if qty <= 0:
                    diag["skip_qty"] = diag.get("skip_qty", 0) + 1
                    if log is not None:
                        log.append({"day": day, "pair": pname, "hm": hm[i], "side": side,
                                    "outcome": "skip_qty"})
                    continue
                sl_pct = max(1.5, min(15.0, 0.75 * float(atr_pct)))
                pos = {
                    "dir": side, "ticker": ticker, "entry": float(px), "qty": qty,
                    "entry_hm": hm[i], "peak": float(px), "atr_pct": float(atr_pct),
                    "trail_mult": trail_mult,
                    "stop": float(px) * (1 - sl_pct / 100.0) if side > 0 else float(px) * (1 + sl_pct / 100.0),
                    "c_exit": float(tparams.get("c_exit", tparams.get("c", 1.5))),
                }
                diag["signals"] = diag.get("signals", 0) + 1
                if log is not None:
                    log.append({"day": day, "pair": pname, "hm": hm[i], "side": side,
                                "outcome": "entered"})
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


# ============================================================== 6. 채점·성과
def score(trades: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recall(이벤트 포착률) · Precision(신호 정밀도) · leg 캡처율."""
    if not events:
        return {}
    matched: Dict[int, Dict[str, Any]] = {}
    tp = 0
    for t in trades:
        hit = None
        for k, e in enumerate(events):
            if e["day"] == t["day"] and e["instrument"] == t["ticker"] \
                    and e["t0"] <= t["entry_hm"] <= e["t1"]:
                hit = k
                break
        if hit is not None:
            tp += 1
            matched.setdefault(hit, t)
    n_ev, n_tr = len(events), len(trades)
    # 보조 지표: 진입이 leg 시작 전이어도 구간과 겹쳤다면 "방향을 맞게 보유" — overlap 포착
    n_overlap = 0
    for e in events:
        if any(t["day"] == e["day"] and t["ticker"] == e["instrument"]
               and t["entry_hm"] <= e["t1"] and t["hm"] >= e["t0"] for t in trades):
            n_overlap += 1
    cap = [matched[k]["gross_pct"] / events[k]["instr_ret_pct"]
           for k in matched if events[k]["instr_ret_pct"]]
    return {"n_events": n_ev, "n_captured": len(matched),
            "recall_pct": round(100.0 * len(matched) / n_ev, 1),
            "recall_overlap_pct": round(100.0 * n_overlap / n_ev, 1),
            "n_trades": n_tr, "n_true": tp, "n_false": n_tr - tp,
            "precision_pct": round(100.0 * tp / n_tr, 1) if n_tr else None,
            "capture_ratio_median": round(float(np.median(cap)), 3) if cap else None,
            "captured_leg_usd": round(sum(abs(events[k]["instr_ret_pct"]) for k in matched) * BUDGET / 100, 2)}


def perf(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0, "pnl_usd": 0.0, "win_pct": None, "pf": None, "mdd_usd": 0.0,
                "fee_usd": 0.0, "fee_share_pct": None, "avg_pct": None, "t_stat": None,
                "days": 0, "pos_days": 0, "per_day": {}, "per_day_avg": 0.0,
                "avg_hold_min": None, "in_market_pct": 0.0}
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
    tstat = (float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r))))
             if len(r) > 2 and r.std(ddof=1) > 0 else None)
    return {"n": len(trades), "pnl_usd": round(float(pnl.sum()), 2),
            "win_pct": round(100.0 * len(wins) / len(trades), 1),
            "pf": round(gw / gl, 2) if gl else None,
            "mdd_usd": round(float((eq - peak).min()), 2),
            "fee_usd": round(fee, 2),
            "fee_share_pct": round(100 * fee / gross_abs, 1) if gross_abs else None,
            "avg_pct": round(float(r.mean()), 3), "t_stat": round(tstat, 2) if tstat is not None else None,
            "days": len(per_day), "pos_days": sum(1 for v in per_day.values() if v > 0),
            "per_day": {k: round(v, 2) for k, v in sorted(per_day.items())},
            "per_day_avg": round(float(pnl.sum()) / max(1, len(per_day)), 2),
            "avg_hold_min": round(float(np.mean([t["hold_min"] for t in trades])), 1),
            "in_market_pct": round(100.0 * sum(t["hold_min"] for t in trades)
                                   / (max(1, len(per_day)) * 390.0 * len(PAIRS)), 1)}


def miss_diagnosis(events: List[Dict[str, Any]], log: List[Dict[str, Any]],
                   trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """미포착 이벤트의 실측 사유 — 트리거가 났는가, 났는데 무엇이 막았는가."""
    reasons: Dict[str, int] = {}
    rows = []
    for e in events:
        side = 1 if e["dir"] == "UP" else -1
        cand = [g for g in log if g["day"] == e["day"] and g["pair"] == e["pair"]
                and e["t0"] <= g["hm"] <= e["t1"] and g["side"] == side]
        captured = any(t["day"] == e["day"] and t["ticker"] == e["instrument"]
                       and e["t0"] <= t["entry_hm"] <= e["t1"] for t in trades)
        if captured:
            why = "포착"
        elif not cand:
            why = "트리거 미발생(leg 안에서 확인 신호 없음)"
        else:
            outs = [g["outcome"] for g in cand]
            why = ("신호 발생·집행했으나 구간 불일치" if "entered" in outs else
                   ("이미 다른 포지션 보유" if "skip_in_position" in outs else
                    ("필터 탈락" if "skip_filter" in outs else
                     ("진입 마감(14:30 이후)" if "skip_late" in outs else "기타"))))
        reasons[why] = reasons.get(why, 0) + 1
        rows.append({"day": e["day"], "instrument": e["instrument"], "t0": e["t0"],
                     "move_pct": e["move_lead_pct"], "reason": why})
    return {"counts": dict(sorted(reasons.items(), key=lambda x: -x[1])), "rows": rows}


# ============================================================== 7. 이론부 실측
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
                rows.append({"valid": False})
                continue
            px_in = float(g["Close"].iloc[j])
            px_out = float(g["Close"].iloc[e["i1"]])
            ret = (px_out / px_in - 1) * 100 - RT * 100
            depart = (px_in / e["instr_px0"] - 1) * 100
            rows.append({"valid": True, "departure_pct": round(depart, 3),
                         "ret_pct": round(ret, 3), "usd": round(ret / 100 * BUDGET, 2),
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


def trigger_quality(prep: Dict[str, Dict[str, Any]], days: List[str],
                    tparams: Dict[str, Any], horizon: int = 6) -> Dict[str, Any]:
    """피벗 트리거 이후 MFE/MAE 분포 — 진입 후 최대 유리/불리 이동(비용 전)."""
    mfe, mae = [], []
    for day in days:
        for pname, (lead, _inv, _thr) in PAIRS.items():
            s = prep.get(f"{pname}|{day}")
            if not s:
                continue
            st: Dict[str, Any] = {}
            for i in range(len(s["hm"])):
                side = trig_pivot(s, i, st, tparams)
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
            "mfe_over_mae": round(float(mfe_a.mean() / abs(mae_a.mean())), 2) if mae_a.mean() else None}


# ============================================================== 8. 현행 엔진(L0)
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
            ok = (float(row["rsi2"]) <= 20.0 and float(row["pct_b"]) <= 0.15
                  and float(row["rvol"]) >= 1.0)
            sig[d][tk] = {"as_of": str(prev.index[-1])[:10], "rsi2": float(row["rsi2"]),
                          "pct_b": float(row["pct_b"]), "rvol": float(row["rvol"]),
                          "atr_pct": float(row["atr_pct"]), "signal": bool(ok)}
    res = CE.sim_core(days, panel, panel5, sig, vix10=vix10, dxy10=dxy10, sentiment=sentiment,
                      budget_mode="current", policy=policy, params=params, label="engine")
    out = []
    for t in res["trades"]:
        if "OPEN_AT_WINDOW_END" in t["reason"]:
            continue
        tk = t["ticker"]
        out.append({"day": t["date"], "hm": str(pd.Timestamp(t["exit_ts"]))[11:16],
                    "entry_hm": str(pd.Timestamp(t["entry_ts"]))[11:16],
                    "pair": "SEMI" if tk in ("SOXL", "SOXS") else "NDX",
                    "ticker": tk, "side": "LONG", "entry": t["entry"], "exit": t["exit"],
                    "qty": t["qty"], "notional_usd": t["notional_usd"], "gross_pct": t["gross_pct"],
                    "ret_pct": t["ret_pct"], "pnl_usd": t["pnl_usd"], "fee_usd": t["fee_usd"],
                    "hold_min": t["hold_min"], "reason": t["reason"]})
    return out


# ============================================================== 9. 사다리·격자 정의
def ladder_configs() -> List[Dict[str, Any]]:
    """포착률을 단계적으로 끌어올리는 탐지기 스펙트럼.

    L0 현행 엔진(2026-11-18 11:37 파라미터)                  — 기준선
    L1 필터 완화(롱 전용)                                    — 현행 DIP 문턱만 완화
    L2 CUSUM 변화점 + 양방향(VIX 게이트·블랙아웃 해제)         — 지시 ①
    L3 L2 + 거래량 급증 + 캔들 반전 꼬리                       — 지시 ①+②
    L4 후행 피벗 확인 c=2.5% + 거래량                        — 지시 ④
    L5 후행 피벗 확인 c=1.5% + 거래량                        — 지시 ④
    L6 후행 피벗 확인 c=1.5%, 필터 없음(대조군)               — 최대 포착률·최저 정밀도
    """
    return [
        {"id": "L0", "name": "현행 엔진", "kind": "engine", "note": "11:37 개정 파라미터(trail 1.5)"},
        {"id": "L1", "name": "필터 완화(롱 전용)", "kind": "sim", "trigger": "dip",
         "params": {"rsi_max": 35.0, "pctb_max": 0.5, "rvol_min": 0.5},
         "filters": (), "blackout": True, "note": "RVOL 필수 해제·RSI2/%B 완화"},
        {"id": "L2", "name": "CUSUM 변화점+양방향", "kind": "sim", "trigger": "cusum",
         "params": {"h": 2.0, "k": 0.15, "conf": 0.35}, "filters": (), "blackout": False,
         "note": "VIX24 숏 게이트 제거·개장 30분 블랙아웃 해제"},
        {"id": "L3", "name": "L2+거래량급증+캔들반전", "kind": "sim", "trigger": "cusum",
         "params": {"h": 2.0, "k": 0.15, "conf": 0.35}, "filters": ("vol", "candle"),
         "blackout": False, "note": "거래량 급증·반전 꼬리 확인 결합"},
        {"id": "L4", "name": "후행피벗 c=2.5%+거래량", "kind": "sim", "trigger": "pivot",
         "params": {"c": 2.5, "c_exit": 2.5}, "filters": ("vol",), "blackout": False,
         "note": "추세 지속형 변곡점만 추종"},
        {"id": "L5", "name": "후행피벗 c=1.5%+거래량", "kind": "sim", "trigger": "pivot",
         "params": {"c": 1.5, "c_exit": 1.5}, "filters": ("vol",), "blackout": False,
         "note": "지그재그 임계와 동일한 확인 폭"},
        {"id": "L6", "name": "후행피벗 c=1.5% 무필터", "kind": "sim", "trigger": "pivot",
         "params": {"c": 1.5, "c_exit": 1.5}, "filters": (), "blackout": False,
         "note": "대조군 — 최대 포착률·최저 정밀도"},
    ]


def c_sweep() -> List[Dict[str, Any]]:
    """주 파라미터 c(%) 격자 — 포착률-순수익 곡선의 원자료."""
    out = []
    for c in (3.0, 2.5, 2.0, 1.5, 1.2, 1.0, 0.75, 0.5, 0.35):
        for flt, fname in (((), "무필터"), (("vol",), "+거래량"),
                           (("vol", "candle", "rsi", "mtf"), "+4필터")):
            out.append({"c": c, "filters": flt, "filter_name": fname})
    return out


def family_sweep() -> List[Dict[str, Any]]:
    """탐지기 계열별 스윕 (지시 ①②④ — 어떤 계열이 포착률을 올리나)."""
    rows = []
    for h in (1.0, 1.5, 2.0, 3.0):
        rows.append({"family": "cusum", "params": {"h": h, "k": 0.15, "conf": 0.35},
                     "label": f"CUSUM h={h}"})
    for z in (1.0, 1.5, 2.0, 2.5):
        rows.append({"family": "zscore", "params": {"z": z}, "label": f"Z-score z={z}"})
    for r, b, v in ((20, 0.15, 1.0), (25, 0.3, 0.7), (35, 0.5, 0.5), (45, 0.7, 0.3)):
        rows.append({"family": "dip", "params": {"rsi_max": r, "pctb_max": b, "rvol_min": v},
                     "label": f"DIP RSI2<={r}/%B<={b}/RVOL>={v}"})
    for c in (2.5, 2.0, 1.5, 1.2, 1.0, 0.5):
        rows.append({"family": "pivot", "params": {"c": c, "c_exit": c}, "label": f"피벗 c={c}%"})
    return rows


# ============================================================== 10. main
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

    prior = json.load(open(PRIOR))["capture"]
    prior_keys = sorted((d, r["instrument"], r["from_hm"], r["to_hm"])
                        for d, rs in prior.items() for r in rs)
    mine_keys = sorted((e["day"], e["instrument"], e["t0"], e["t1"]) for e in ev_5d)
    event_set_match = prior_keys == mine_keys

    result: Dict[str, Any] = {
        "generated_at_kst": pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S"),
        "window": {"5d": DAYS5,
                   "60d": {"start": all_days[0], "end": all_days[-1], "sessions": len(all_days)}},
        "cost_round_trip_pct": RT * 100, "budget_usd": BUDGET,
        "engine_params_snapshot": {"trail_atr_mult": params.get("trail_atr_mult"),
                                   "initial_sl_atr_mult": params.get("initial_sl_atr_mult"),
                                   "updated_at_kst": params.get("updated_at_kst"),
                                   "source": params.get("source")},
        "event_set": {
            "n_5d": len(ev_5d), "n_60d": len(ev_all), "matches_prior_24": bool(event_set_match),
            "sum_abs_instr_ret_5d": round(sum(abs(e["instr_ret_pct"]) for e in ev_5d), 1),
            "sum_abs_instr_ret_60d": round(sum(abs(e["instr_ret_pct"]) for e in ev_all), 1),
            "median_abs_instr_ret_60d": round(float(np.median([abs(e["instr_ret_pct"])
                                                               for e in ev_all])), 2),
            "oracle_usd_5d": round(sum(abs(e["instr_ret_pct"]) for e in ev_5d) * BUDGET / 100
                                   - len(ev_5d) * BUDGET * RT, 2),
            "oracle_usd_60d": round(sum(abs(e["instr_ret_pct"]) for e in ev_all) * BUDGET / 100
                                    - len(ev_all) * BUDGET * RT, 2),
            "per_day_60d": round(len(ev_all) / len(all_days), 2)},
        "events_5d": ev_5d,
    }

    result["theory"] = {
        "oracle_delay_5d": delay_analysis(panel, ev_5d),
        "oracle_delay_60d": delay_analysis(panel, ev_all),
        "trigger_quality_60d": {
            f"c{c}": trigger_quality(prep_all, all_days, {"c": c})
            for c in (3.0, 2.5, 2.0, 1.5, 1.2, 1.0, 0.75, 0.5, 0.35)},
    }

    # ---------------------------------------------------- 사다리
    ladder_rows = []
    for cfg in ladder_configs():
        variants = [("engine", 1.5)] if cfg["kind"] == "engine" else [("trail", 1.5), ("flip", 1.5)]
        for exit_mode, tm in variants:
            if cfg["kind"] == "engine":
                tr_all = run_engine(panel, panel5, all_days, vix10, dxy10, sentiment, policy, params)
            else:
                tr_all = simulate(prep_all, dt_all, all_days, cfg["trigger"], cfg["params"],
                                  exit_mode, filters=cfg["filters"], blackout=cfg["blackout"],
                                  trail_mult=tm)
            tr_5d = [t for t in tr_all if t["day"] in DAYS5]
            s5, s60 = score(tr_5d, ev_5d), score(tr_all, ev_all)
            ladder_rows.append({
                "id": cfg["id"], "name": cfg["name"], "note": cfg.get("note", ""),
                "exit": exit_mode, "trail_atr_mult": tm,
                "params": cfg.get("params", {}), "filters": list(cfg.get("filters", ())),
                "blackout": cfg.get("blackout", True),
                "5d": {**perf(tr_5d), **{f"score_{k}": v for k, v in s5.items()}},
                "60d": {**perf(tr_all), **{f"score_{k}": v for k, v in s60.items()}}})
            print(f"{cfg['id']}/{exit_mode}: 5d n={len(tr_5d)} pnl={perf(tr_5d)['pnl_usd']} "
                  f"R={s5.get('recall_pct')}% P={s5.get('precision_pct')} | "
                  f"60d n={len(tr_all)} pnl={perf(tr_all)['pnl_usd']} R={s60.get('recall_pct')}% "
                  f"P={s60.get('precision_pct')} fee%={perf(tr_all)['fee_share_pct']}", flush=True)
    result["ladder"] = ladder_rows

    # ---------------------------------------------------- c 격자 (출구 3종)
    grid_rows = []
    for g in c_sweep():
        for exit_mode, tm in (("trail", 1.5), ("trail", 0.9), ("flip", 1.5)):
            tp = {"c": g["c"], "c_exit": g["c"]}
            tr_all = simulate(prep_all, dt_all, all_days, "pivot", tp, exit_mode,
                              filters=g["filters"], blackout=False, trail_mult=tm)
            tr_5d = [t for t in tr_all if t["day"] in DAYS5]
            s5, s60 = score(tr_5d, ev_5d), score(tr_all, ev_all)
            grid_rows.append({"c": g["c"], "filters": list(g["filters"]),
                              "filter_name": g["filter_name"], "exit": f"{exit_mode}{tm}",
                              "exit_mode": exit_mode, "trail_atr_mult": tm,
                              "5d": {**perf(tr_5d), **{f"score_{k}": v for k, v in s5.items()}},
                              "60d": {**perf(tr_all), **{f"score_{k}": v for k, v in s60.items()}}})
            print(f"c={g['c']} {g['filter_name']}/{exit_mode}{tm}: 5d n={len(tr_5d)} "
                  f"pnl={perf(tr_5d)['pnl_usd']} R={s5.get('recall_pct')}% | "
                  f"60d n={len(tr_all)} pnl={perf(tr_all)['pnl_usd']} R={s60.get('recall_pct')}% "
                  f"P={s60.get('precision_pct')}", flush=True)
    result["grid"] = grid_rows

    # ---------------------------------------------------- 계열별 스윕
    fam_rows = []
    for f in family_sweep():
        for exit_mode, tm in (("trail", 1.5), ("flip", 1.5)):
            tr_all = simulate(prep_all, dt_all, all_days, f["family"], f["params"], exit_mode,
                              filters=(), blackout=False, trail_mult=tm)
            tr_5d = [t for t in tr_all if t["day"] in DAYS5]
            s5, s60 = score(tr_5d, ev_5d), score(tr_all, ev_all)
            fam_rows.append({"family": f["family"], "label": f["label"], "params": f["params"],
                             "exit": exit_mode,
                             "5d": {**perf(tr_5d), **{f"score_{k}": v for k, v in s5.items()}},
                             "60d": {**perf(tr_all), **{f"score_{k}": v for k, v in s60.items()}}})
            print(f"{f['label']}/{exit_mode}: 5d n={len(tr_5d)} pnl={perf(tr_5d)['pnl_usd']} "
                  f"R={s5.get('recall_pct')}% | 60d n={len(tr_all)} pnl={perf(tr_all)['pnl_usd']} "
                  f"R={s60.get('recall_pct')}%", flush=True)
    result["family_sweep"] = fam_rows

    # ---------------------------------------------------- 포착률 상한 진단
    diag: Dict[str, Any] = {}
    log: List[Dict[str, Any]] = []
    tr_hi = simulate(prep_all, dt_all, all_days, "pivot", {"c": 1.5, "c_exit": 1.5}, "flip",
                     filters=(), blackout=False, diag=diag, log=log)
    result["recall_ceiling"] = {
        "config": "후행피벗 c=1.5% 무필터 · 반전청산",
        "5d": score([t for t in tr_hi if t["day"] in DAYS5], ev_5d),
        "60d": score(tr_hi, ev_all), "diag": diag,
        "miss_60d": miss_diagnosis(ev_all, log, tr_hi),
        "miss_5d": miss_diagnosis(ev_5d, log, tr_hi)}

    # ---------------------------------------------------- 5분봉 판정 격자 (지시 ④)
    prep5 = prep_5m(all_days)
    five = []
    for c in (1.5, 1.0, 0.75, 0.5, 0.35):
        for exit_mode in ("trail", "flip"):
            tr = simulate(prep5, dt_all, all_days, "pivot", {"c": c, "c_exit": c}, exit_mode,
                          filters=(), blackout=False, trail_mult=1.5)
            t5 = [t for t in tr if t["day"] in DAYS5]
            s5, s60 = score(t5, ev_5d), score(tr, ev_all)
            five.append({"tf": "5m", "c": c, "exit": exit_mode,
                         "5d": {**perf(t5), **{f"score_{k}": v for k, v in s5.items()}},
                         "60d": {**perf(tr), **{f"score_{k}": v for k, v in s60.items()}}})
            print(f"5m c={c} {exit_mode}: 5d n={len(t5)} pnl={perf(t5)['pnl_usd']} "
                  f"R={s5.get('recall_pct')}% P={s5.get('precision_pct')} | 60d n={len(tr)} "
                  f"pnl={perf(tr)['pnl_usd']} R={s60.get('recall_pct')}% "
                  f"P={s60.get('precision_pct')} fee%={perf(tr)['fee_share_pct']}", flush=True)
    result["five_min_grid"] = five

    # ---------------------------------------------------- 강건성: 30/30 분할 + 슬리피지
    half = len(all_days) // 2
    d_a, d_b = all_days[:half], all_days[half:]
    ev_a = [e for e in ev_all if e["day"] in set(d_a)]
    ev_b = [e for e in ev_all if e["day"] in set(d_b)]
    robust = []
    for c in (3.0, 2.5, 2.0, 1.5):
        for flt, fname in (((), "무필터"), (("vol",), "+거래량")):
            for exit_mode in ("trail", "flip"):
                tp = {"c": c, "c_exit": c}
                ta = simulate(prep_all, dt_all, d_a, "pivot", tp, exit_mode, filters=flt,
                              blackout=False, trail_mult=1.5)
                tb = simulate(prep_all, dt_all, d_b, "pivot", tp, exit_mode, filters=flt,
                              blackout=False, trail_mult=1.5)
                tr_slip = simulate(prep_all, dt_all, all_days, "pivot", tp, exit_mode, filters=flt,
                                   blackout=False, trail_mult=1.5, rt=RT + 0.0024)
                robust.append({
                    "c": c, "filters": fname, "exit": exit_mode,
                    "first30": {**perf(ta), **{f"score_{k}": v for k, v in score(ta, ev_a).items()}},
                    "last30": {**perf(tb), **{f"score_{k}": v for k, v in score(tb, ev_b).items()}},
                    "slippage_rt_0p48_pnl_usd": round(perf(tr_slip)["pnl_usd"], 2),
                })
                print(f"robust c={c} {fname}/{exit_mode}: A={perf(ta)['pnl_usd']} "
                      f"R={score(ta, ev_a).get('recall_pct')}% | B={perf(tb)['pnl_usd']} "
                      f"R={score(tb, ev_b).get('recall_pct')}% | slip={perf(tr_slip)['pnl_usd']}", flush=True)
    result["robustness"] = robust

    # ---------------------------------------------------- 포착률 구간별 순수익 (60일)
    bands = [(0, 10), (10, 30), (30, 50), (50, 70), (70, 90), (90, 100.1)]
    band_rows = []
    for lo, hi in bands:
        sel = [r for r in grid_rows if lo <= r["60d"]["score_recall_pct"] < hi]
        if not sel:
            continue
        band_rows.append({
            "band_pct": f"{lo}~{hi if hi <= 100 else 100}", "n_configs": len(sel),
            "mean_pnl_usd": round(float(np.mean([r["60d"]["pnl_usd"] for r in sel])), 2),
            "median_pnl_usd": round(float(np.median([r["60d"]["pnl_usd"] for r in sel])), 2),
            "best_pnl_usd": round(max(r["60d"]["pnl_usd"] for r in sel), 2),
            "mean_trades": int(round(float(np.mean([r["60d"]["n"] for r in sel])))),
            "mean_precision_pct": round(float(np.mean([r["60d"]["score_precision_pct"] for r in sel
                                                       if r["60d"]["score_precision_pct"] is not None])), 1),
            "mean_fee_share_pct": round(float(np.mean([r["60d"]["fee_share_pct"] for r in sel
                                                       if r["60d"]["fee_share_pct"] is not None])), 1)})
    rec = np.array([r["60d"]["score_recall_pct"] for r in grid_rows], float)
    pn = np.array([r["60d"]["pnl_usd"] for r in grid_rows], float)
    slope, intercept = np.polyfit(rec, pn, 1)
    result["recall_band_analysis"] = {
        "bands_60d": band_rows,
        "corr_recall_vs_pnl_60d": round(float(np.corrcoef(rec, pn)[0, 1]), 3),
        "slope_usd_per_1pct_recall": round(float(slope), 2),
        "fit": f"pnl = {intercept:.2f} + {slope:.2f} × recall"}

    # ---------------------------------------------------- 스윗스팟
    best: Dict[str, Any] = {}
    for row in grid_rows:
        for w in ("5d", "60d"):
            key = f"{row['filter_name']}|{row['exit']}|{w}"
            v = row[w]["pnl_usd"]
            if key not in best or v > best[key]["pnl_usd"]:
                best[key] = {"c": row["c"], "pnl_usd": v,
                             "recall_pct": row[w]["score_recall_pct"],
                             "precision_pct": row[w]["score_precision_pct"], "n": row[w]["n"],
                             "pf": row[w]["pf"], "mdd_usd": row[w]["mdd_usd"],
                             "exit": row["exit"], "filters": row["filter_name"], "window": w}
    result["sweet_spot_by_variant"] = best

    top60 = sorted(grid_rows, key=lambda r: -r["60d"]["pnl_usd"])[:8]
    result["top_60d_configs"] = [{"c": r["c"], "filters": r["filter_name"], "exit": r["exit"],
                                  "recall_pct": r["60d"]["score_recall_pct"],
                                  "precision_pct": r["60d"]["score_precision_pct"],
                                  "n": r["60d"]["n"], "pnl_usd": r["60d"]["pnl_usd"],
                                  "pf": r["60d"]["pf"], "mdd_usd": r["60d"]["mdd_usd"],
                                  "fee_share_pct": r["60d"]["fee_share_pct"]} for r in top60]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print("saved", OUT)
    print("event set matches prior 24:", event_set_match)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
