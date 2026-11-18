"""10분봉 롱·숏 변곡점 후보 알고리즘 (2026-11-18 A/B 연구용).

공통 규약
---------
- 신호는 10분봉 종가에서 계산하고 **같은 봉 종가에 체결**한다(룩어헤드 없음).
- 롱은 정방향(SOXL·TQQQ) 매수, 숏은 인버스(SOXS·SQQQ) 매수 — 현금계좌에서 실행 가능한 형태.
- 왕복 0.24%(편도 12bp) 차감. 정수주($150 예산)와 명목 $150(소수) 두 가지로 계산.
- 하루 1포지션(동시 보유 1개), 15:50 ET 전 강제청산 — 오버나이트 없음.

후보
----
C1 VWAP·볼린저 회귀 스캘핑 (양방향)
C2 10분 EMA 크로스 + ADX/RVOL 추세추종 (양방향)
C3 RSI(2) 극단 + 직전 봉 반전 확인 (양방향 스위칭)
C4 10분 Donchian 돌파 추세지속 (VWAP 필터, 양방향)
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

import i10_lib as L

EOD_MIN = 15 * 60 + 50          # 15:50 ET 강제청산


def _prep(bars: pd.DataFrame) -> pd.DataFrame:
    d = bars.copy()
    d["ma9"] = d["Close"].ewm(span=9, adjust=False).mean()
    d["ma21"] = d["Close"].ewm(span=21, adjust=False).mean()
    d["rsi2"] = L.rsi(d["Close"], 2)
    d["rsi14"] = L.rsi(d["Close"], 14)
    d["atr"] = L.atr(d, 14)
    d["adx"] = L.adx(d, 14)
    d["vol_ma"] = d["Volume"].rolling(20).mean().shift(1)
    d["rvol"] = d["Volume"] / d["vol_ma"].replace(0, np.nan)
    d["prev_high"] = d["High"].shift(1)
    d["prev_low"] = d["Low"].shift(1)
    d["don_hi"] = d["High"].rolling(12).max().shift(1)
    d["don_lo"] = d["Low"].rolling(12).min().shift(1)
    d["don_hi6"] = d["High"].rolling(6).max().shift(1)
    d["don_lo6"] = d["Low"].rolling(6).min().shift(1)
    return d


def _vwap_state(d: pd.DataFrame) -> pd.DataFrame:
    out = []
    for day, g in d.groupby("session"):
        g = g.copy()
        tp = (g["High"] + g["Low"] + g["Close"]) / 3.0
        v = g["Volume"].fillna(0).astype(float)
        cum_v = v.cumsum().replace(0, np.nan)
        g["vwap"] = (tp * v).cumsum() / cum_v
        dev = (g["Close"] - g["vwap"])
        g["vwap_sd"] = dev.expanding(min_periods=6).std()
        g["vwap_z"] = dev / g["vwap_sd"].replace(0, np.nan)
        out.append(g)
    return pd.concat(out).sort_index()


# ------------------------------------------------------------------ signals
def sig_vwap_reversion(d: pd.DataFrame, i: int, p: Dict[str, Any]) -> Optional[str]:
    z = d["vwap_z"].iloc[i]
    if pd.isna(z):
        return None
    c, pc = d["Close"].iloc[i], d["Close"].iloc[i - 1]
    if z <= -p["z"] and c > pc:
        return "L"
    if z >= p["z"] and c < pc:
        return "S"
    return None


def sig_ema_trend(d: pd.DataFrame, i: int, p: Dict[str, Any]) -> Optional[str]:
    a, b = d["ma9"].iloc[i - 1], d["ma21"].iloc[i - 1]
    a2, b2 = d["ma9"].iloc[i], d["ma21"].iloc[i]
    if any(pd.isna(x) for x in (a, b, a2, b2)):
        return None
    if d["adx"].iloc[i] < p["adx_min"]:
        return None
    rv = d["rvol"].iloc[i]
    if p.get("rvol_min") and (pd.isna(rv) or rv < p["rvol_min"]):
        return None
    if a <= b and a2 > b2:
        return "L"
    if a >= b and a2 < b2:
        return "S"
    return None


def sig_rsi_reversal(d: pd.DataFrame, i: int, p: Dict[str, Any]) -> Optional[str]:
    """RSI2 극단 → 반전 확인(직전 봉보다 종가가 되돌림) 시 진입.

    직전 봉 RSI2가 극단(≤5 / ≥95)이고 이번 봉 종가가 반대쪽으로 움직인 첫 봉에서 진입.
    """
    r_prev, r = d["rsi2"].iloc[i - 1], d["rsi2"].iloc[i]
    if pd.isna(r) or pd.isna(r_prev):
        return None
    c, pc = d["Close"].iloc[i], d["Close"].iloc[i - 1]
    if r_prev <= p["rsi_lo"] and c > pc:
        return "L"
    if r_prev >= p["rsi_hi"] and c < pc:
        return "S"
    return None


def sig_donchian(d: pd.DataFrame, i: int, p: Dict[str, Any]) -> Optional[str]:
    c = d["Close"].iloc[i]
    if pd.isna(d["don_hi"].iloc[i]) or pd.isna(d["vwap"].iloc[i]):
        return None
    if c > d["don_hi"].iloc[i] and c > d["vwap"].iloc[i]:
        return "L"
    if c < d["don_lo"].iloc[i] and c < d["vwap"].iloc[i]:
        return "S"
    return None


# ------------------------------------------------------------------ engine
def simulate(strategy: str, under: pd.DataFrame, inv: pd.DataFrame, days: List[str],
             p: Dict[str, Any], under_sym: str = "SOXL", inv_sym: str = "SOXS",
             budget: float = 150.0, rt: float = L.RT_COST,
             integer: bool = True) -> Dict[str, Any]:
    sig_fn = {"C1": sig_vwap_reversion, "C2": sig_ema_trend,
              "C3": sig_rsi_reversal, "C4": sig_donchian}[strategy]
    du = _prep(_vwap_state(under))
    dinv = inv.copy()
    trades: List[Dict[str, Any]] = []
    for day in days:
        g = du[du["session"] == day]
        gi = dinv[dinv["session"] == day]
        if len(g) < 5 or not len(gi):
            continue
        inv_px = {str(ts): float(px) for ts, px in gi["Close"].items()}
        pos: Optional[Dict[str, Any]] = None
        for i in range(1, len(g)):
            ts = g.index[i]
            close = float(g["Close"].iloc[i])
            hr = int(g["hm_end"].iloc[i].split(":")[0]) * 60 + int(g["hm_end"].iloc[i].split(":")[1])
            ts_end = ts + pd.Timedelta(minutes=10)
            if pos:
                tk = pos["ticker"]
                px = close if tk == pos["sym"] else inv_px.get(str(ts))
                if px:
                    pos["peak"] = max(pos["peak"], px) if pos["dir"] == "L" else min(pos["peak"], px)
                    atr_abs = px * pos["atr_pct"]        # 종목가 기준 ATR%(가격 스케일 불변)
                    act = None
                    if hr >= EOD_MIN:
                        act = ("EOD", px)
                    elif pos["dir"] == "L" and px <= pos["stop"]:
                        act = ("STOP", px)
                    elif pos["dir"] == "S" and px >= pos["stop"]:
                        act = ("STOP", px)
                    elif pos.get("trail") is not None:
                        if pos["dir"] == "L" and px <= pos["peak"] - pos["trail"] * atr_abs:
                            act = ("TRAIL", px)
                        if pos["dir"] == "S" and px >= pos["peak"] + pos["trail"] * atr_abs:
                            act = ("TRAIL", px)
                    if not act and pos.get("max_hold") and (i - pos["i"]) >= pos["max_hold"]:
                        act = ("MAXHOLD", px)
                    if not act and pos.get("exit_fn"):
                        if pos["exit_fn"](g, i, pos):
                            act = ("SIGNAL_EXIT", px)
                    if act:
                        reason, px = act
                        trades.append(L.make_trade(day, tk, "LONG", pos["ts"], ts_end, pos["entry"], px,
                                                   pos["qty"], reason, rt_cost=rt))
                        pos = None
            if pos is None and hr < EOD_MIN:
                s = sig_fn(g, i, p)
                if s:
                    tk = under_sym if s == "L" else inv_sym
                    px = close if s == "L" else inv_px.get(str(ts))
                    if px:
                        qty = int(budget // px) if integer else 1
                        if qty <= 0:
                            qty = 1
                        notional = px * (qty if integer else budget / px)
                        atr_pct = (float(g["atr"].iloc[i]) / close) if not pd.isna(g["atr"].iloc[i]) else 0.01
                        dip = p.get("stop_atr", 1.0)
                        pos = {"dir": s, "ticker": tk, "sym": under_sym, "entry": px,
                               "qty": qty, "ts": str(ts_end), "i": i, "peak": px, "atr_pct": atr_pct,
                               "stop": px * (1 - dip * atr_pct) if s == "L" else px * (1 + dip * atr_pct),
                               "trail": p.get("trail_atr"), "max_hold": p.get("max_hold"),
                               "notional": notional,
                               "exit_fn": (p.get("exit_fn") if p.get("exit_fn") else None)}
                        if p.get("exit_mode") == "vwap":
                            pos["exit_fn"] = lambda gg, ii, pp, s=s: (
                                (s == "L" and gg["Close"].iloc[ii] >= gg["vwap"].iloc[ii]) or
                                (s == "S" and gg["Close"].iloc[ii] <= gg["vwap"].iloc[ii]))
                        elif p.get("exit_mode") == "rsi50":
                            pos["exit_fn"] = lambda gg, ii, pp, s=s: (
                                (s == "L" and gg["rsi2"].iloc[ii] > 50) or
                                (s == "S" and gg["rsi2"].iloc[ii] < 50))
                        elif p.get("exit_mode") == "don6":
                            pos["exit_fn"] = lambda gg, ii, pp, s=s: (
                                (s == "L" and gg["Close"].iloc[ii] < gg["don_lo6"].iloc[ii]) or
                                (s == "S" and gg["Close"].iloc[ii] > gg["don_hi6"].iloc[ii])
                                if not pd.isna(gg["don_lo6"].iloc[ii]) else False)
        # 세션 종료 시 잔여 포지션은 15:50에 이미 청산(EOD)
    stats = L.trade_stats(trades, budget)
    return {"strategy": strategy, "params": {k: v for k, v in p.items() if k != "exit_fn"},
            "trades": trades, "stats": stats}


# ------------------------------------------------------------------ configs
CONFIGS: Dict[str, Dict[str, Any]] = {
    "C1": {"z": 2.0, "stop_atr": 1.0, "trail_atr": None, "max_hold": 6, "exit_mode": "vwap"},
    "C2": {"adx_min": 22.0, "rvol_min": 1.0, "stop_atr": 1.5, "trail_atr": 1.5, "max_hold": None,
           "exit_mode": None},
    "C3": {"rsi_lo": 5.0, "rsi_hi": 95.0, "stop_atr": 1.5, "trail_atr": None, "max_hold": 12,
           "exit_mode": "rsi50"},
    "C4": {"stop_atr": 1.2, "trail_atr": 1.2, "max_hold": None, "exit_mode": "don6"},
}


# ------------------------------------------------------------------ C5 저빈도 대안
def simulate_gap_day(under: pd.DataFrame, inv: pd.DataFrame, days: List[str],
                     entry_hm: str = "10:00", exit_hm: str = "15:40", stop_atr: float = 3.0,
                     budget: float = 150.0, rt: float = L.RT_COST,
                     under_sym: str = "SOXL", inv_sym: str = "SOXS") -> Dict[str, Any]:
    """갭 방향 1일 1회 추종 — 전일 종가 대비 당일 10:00 가격의 부호로 방향 결정.

    · 방향: 10:00 가격 > 전일 종가 → 정방향(SOXL/TQQQ), 미만 → 인버스(SOXS/SQQQ)
    · 진입 10:00 ET(개장 유예 직후), 청산 15:40 ET(신중 모드 강제청산 시각), 손절 3×ATR
    · 10분봉 신호 왕복이 아니라 하루 1회 왕복이라 수수료 비중이 1/6~1/10로 떨어진다.
    """
    trades: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for day in sorted(under["session"].unique()):
        g = under[under["session"] == day]
        gi = inv[inv["session"] == day]
        if len(g) < 5 or not len(gi):
            prev_close = float(g["Close"].iloc[-1]) if len(g) else prev_close
            continue
        if prev_close is None:
            prev_close = float(g["Close"].iloc[-1])
            continue
        ei_l = [i for i, h in enumerate(g["hm_end"]) if h == entry_hm]
        xi_l = [i for i, h in enumerate(g["hm_end"]) if h == exit_hm]
        if not ei_l or not xi_l:
            prev_close = float(g["Close"].iloc[-1])
            continue
        ei, xi = ei_l[0], xi_l[0]
        tr = pd.concat([g["High"] - g["Low"], (g["High"] - g["Close"].shift()).abs(),
                        (g["Low"] - g["Close"].shift()).abs()], axis=1).max(axis=1)
        atr_pct = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[ei] / g["Close"].iloc[ei])
        o = float(g["Close"].iloc[ei])
        sgn = 1 if o > prev_close else -1
        tk = under_sym if sgn > 0 else inv_sym
        gg = g if sgn > 0 else gi
        px_in = float(gg["Close"].iloc[ei])
        qty = max(1, int(budget // px_in))
        stop = px_in * (1 - stop_atr * atr_pct) if sgn > 0 else px_in * (1 + stop_atr * atr_pct)
        exit_px = float(gg["Close"].iloc[xi])
        reason = "EOD"
        for k in range(ei + 1, xi + 1):
            q = float(gg["Close"].iloc[k])
            if (sgn > 0 and q <= stop) or (sgn < 0 and q >= stop):
                exit_px, reason = q, "STOP"
                break
        if day in days:
            trades.append(L.make_trade(day, tk, "LONG", str(gg.index[ei] + pd.Timedelta(minutes=10)),
                                       str(gg.index[xi] + pd.Timedelta(minutes=10)),
                                       px_in, exit_px, qty, reason, rt_cost=rt))
        prev_close = float(g["Close"].iloc[-1])
    return {"strategy": "C5", "params": {"entry_hm": entry_hm, "exit_hm": exit_hm, "stop_atr": stop_atr},
            "trades": trades, "stats": L.trade_stats(trades, budget)}
