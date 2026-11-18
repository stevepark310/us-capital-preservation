"""
Short / Inverse Desk — 숏·인버스 매수 검토 엔진 (2026-11-17)

동훈님 지시: "다른 종목들 예를 들어 숏 종목 등 다른 종목에 대해서도 매수 검토해봐."

이 모듈이 하는 일
----------------
1. 인버스·숏 ETF 유니버스를 실측으로 스크리닝한다(가격·유동성·ATR%·1년 감쇠율).
2. 레짐(VIX 구간)별 3세션 기대수익과, 신호×브래킷 조합의 경로 기반 백테스트를 돌린다
   (2년 60분봉, 왕복 0.24% 마찰).
3. NVDA 실적 반응일 이벤트 스터디(10년 39회)로 유일하게 살아남은 구조를 검증한다.
4. 남은 현금으로 살 수 있는 1주 단위 헤지 사이즈와 계좌 노출 변화를 계산한다.
5. 공격적으로 하지 말아야 할 후보는 탈락 사유와 실측치를 그대로 남긴다.

검증 강도 표기 규칙
------------------
- `낮음`: 표본 < 30 또는 t < 1.0
- `중간`: 표본 >= 30, 1.0 <= t < 2.0
- `높음`: t >= 2.0
(다중검정: 그리드 스윕의 최고 셀은 t가 2를 넘어도 채택하지 않는다. 파라미터 이웃에서
 부호가 유지되는지, 두 개 이상 종목에서 같은 방향인지를 함께 본다.)

주의: 이 모듈은 검토·계산만 한다. 주문은 넣지 않는다(`scripts/monitor_and_exit.py`가
승인된 브래킷만 집행). 실전 원장과 시뮬 수치는 분리해 표기한다.
"""
from __future__ import annotations

import json
import os
import pickle
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
CACHE = os.path.join(BASE_DIR, "data_cache")
DAILY_CACHE = os.path.join(CACHE, "short_desk_daily.pkl")
HOUR_CACHE = os.path.join(CACHE, "short_desk_60m.pkl")
MIN5_CACHE = os.path.join(CACHE, "short_desk_5m.pkl")
TENY_CACHE = os.path.join(CACHE, "short_desk_10y.pkl")
REVIEW_PATH = os.path.join(DATA, "short_desk_review.json")

ROUND_TRIP_FEE_PCT = 0.24          # NH 수수료 0.09% + 슬리피지 0.15%
VIX_GATE = 24.0                    # 기존 BEAR ALPHA 게이트(변경 금지)
EARNINGS_GAP_TRIGGER_PCT = 2.0     # 반응일 갭업 트리거(실측 최적 조건)
DISASTER_STOP_PCT = 8.0            # 반응일 숏의 재난 손절(실측 최악 -7.6%)

# 인버스 유니버스 메타데이터 — 2026-11-17 yfinance 실측
UNIVERSE: Dict[str, Dict[str, Any]] = {
    "SOXS": {"name": "Direxion Daily Semiconductor Bear 3X", "lev": -3, "expense_pct": 1.00,
             "underlying": "SOX(반도체)", "pair": "SOXL", "adv_usd_m": 5042.5,
             "role": "반도체 급락 추종(현 보유 SOXL의 직접 헤지)"},
    "SQQQ": {"name": "ProShares UltraPro Short QQQ", "lev": -3, "expense_pct": 0.94,
             "underlying": "NASDAQ-100", "pair": "TQQQ", "adv_usd_m": 2421.2,
             "role": "나스닥 하락 추종(현 보유 TQQQ의 직접 헤지)"},
    "SPXS": {"name": "Direxion Daily S&P500 Bear 3X", "lev": -3, "expense_pct": 0.95,
             "underlying": "S&P500", "pair": "SPXL", "adv_usd_m": 357.1, "role": "지수 전반 3배 숏"},
    "SPXU": {"name": "ProShares UltraPro Short S&P500", "lev": -3, "expense_pct": 0.89,
             "underlying": "S&P500", "pair": "UPRO", "adv_usd_m": 395.7, "role": "SPXS와 동일 계열"},
    "PSQ": {"name": "ProShares Short QQQ", "lev": -1, "expense_pct": 0.95,
            "underlying": "NASDAQ-100", "pair": "QQQ", "adv_usd_m": 657.8, "role": "1배 숏(감쇠 최소)"},
    "SH": {"name": "ProShares Short S&P500", "lev": -1, "expense_pct": 0.89,
           "underlying": "S&P500", "pair": "SPY", "adv_usd_m": 365.8, "role": "1배 숏(감쇠 최소)"},
    "NVDS": {"name": "Tradr 1.5X Short NVDA Daily", "lev": -1.5, "expense_pct": 1.05,
             "underlying": "NVDA", "pair": "NVDL", "adv_usd_m": 10.2,
             "role": "NVDA 개별 헤지 — 유동성 얇음(ADV $10M)"},
    "TZA": {"name": "Direxion Daily Small Cap Bear 3X", "lev": -3, "expense_pct": 1.04,
            "underlying": "Russell 2000", "pair": "TNA", "adv_usd_m": 243.7, "role": "소형주 숏"},
    "TECS": {"name": "Direxion Daily Technology Bear 3X", "lev": -3, "expense_pct": 1.05,
             "underlying": "Technology Select Sector", "pair": "TECL", "adv_usd_m": 31.9, "role": "기술주 숏"},
    "FNGD": {"name": "MicroSectors FANG+ Index -3X", "lev": -3, "expense_pct": 0.95,
             "underlying": "FANG+", "pair": "FNGU", "adv_usd_m": 17.5, "role": "빅테크 숏"},
    "LABD": {"name": "Direxion Daily S&P Biotech Bear 3X", "lev": -3, "expense_pct": 1.02,
             "underlying": "S&P Biotech", "pair": "LABU", "adv_usd_m": 178.2, "role": "바이오 숏"},
    "WEBS": {"name": "Direxion Daily Dow Jones Internet Bear 3X", "lev": -3, "expense_pct": 1.09,
             "underlying": "Dow Jones Internet", "pair": "WEBL", "adv_usd_m": 0.7, "role": "유동성 부족 — 검토 제외"},
    "YANG": {"name": "Direxion Daily FTSE China Bear 3X", "lev": -3, "expense_pct": 1.16,
             "underlying": "FTSE China 50", "pair": "YINN", "adv_usd_m": 24.2, "role": "중국 숏"},
}

# 기존 게이트·트리거 현황
DOWNTURN_GATES = {
    "vix_gate": VIX_GATE,
    "confirmations_required": 2,
    "cube_lei_late_cycle": 102.0,
    "vix_now": 22.91,
    "gate_status": "미달 — VIX 22.91 < 24.0 (2026-11-17 11:53 KST 기준)",
}

# 실측으로 탈락시킨 후보 (사유·수치 보존)
REJECTED: List[Dict[str, Any]] = [
    {"candidate": "VIX≥24 게이트 후 SOXS 매수(TP3.5/SL1.8, 2세션)",
     "n": 52, "win_pct": 17.3, "ev_pct": -1.12,
     "reason": "VIX 급등 후 평균회귀가 지배. 3배 상품에 1.8% 손절은 일일 변동(9.4%)의 0.2배로 노이즈 손절."},
    {"candidate": "VIX≥24 게이트 후 SQQQ 매수(TP2.8/SL1.5, 2세션)",
     "n": 52, "win_pct": 32.7, "ev_pct": -0.33, "reason": "동일 사유(변동성 대비 손절 폭 과소)."},
    {"candidate": "갭다운 지속(SOXL 시가갭 -2%↓ → SOXS 당일 보유)",
     "n": 140, "win_pct": 43.6, "ev_pct": -0.42, "reason": "갭 후 반등이 더 잦음. 갭 방향 추종은 기대값 음수."},
    {"candidate": "5분봉 ORB(지수 OR 이탈 → 인버스 롱, RVOL 1.0~1.5, OR 5/15/30분)",
     "n": "4~22건/변형", "win_pct": "22~100", "ev_pct": "-0.52~+1.79",
     "reason": "60일 표본에서 변형별 거래 수가 4~22건으로 통계 불가. 좋아 보이는 셀은 소표본 우연."},
    {"candidate": "SQQQ, QQQ<EMA20 & VIX≥22, TP6/SL3/5세션 (그리드 최고 셀)",
     "n": 54, "win_pct": 42.6, "ev_pct": 0.555,
     "reason": "189조합 스윕의 최고값. t≈1.6, 파라미터 이웃에서 부호 유지 안 됨 → 다중검정 과적합으로 판정."},
    {"candidate": "NVDS 매수 후 실적 오버나이트 보유",
     "n": 17, "win_pct": 64.7, "ev_pct": 2.01,
     "reason": "NVDS는 1.5배 인버스(NVDA -1.5x)에 ADV $10M로 얇고, 오버나이트 갭 리스크가 비대칭(최악 -13.4%). "
               "반응일 세션 내 거래로 대체."},
]


# ------------------------------------------------------------------ 데이터
def _load_pkl(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return {}


def load_frames(interval: str = "1d") -> Dict[str, pd.DataFrame]:
    path = {"1d": DAILY_CACHE, "60m": HOUR_CACHE, "5m": MIN5_CACHE}[interval]
    return (_load_pkl(path) or {}).get("frames") or {}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


# ------------------------------------------------------------------ 지표
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(series: pd.Series, n: int = 2) -> pd.Series:
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def enrich(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    c = f["Close"]
    f["ema20"] = ema(c, 20)
    f["ema50"] = ema(c, 50)
    f["sma200"] = c.rolling(200).mean()
    f["rsi2"] = rsi(c, 2)
    tr = pd.concat([f["High"] - f["Low"], (f["High"] - c.shift()).abs(),
                    (f["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.rolling(14).mean() / c * 100
    f["ret1"] = c.pct_change()
    f["ret5"] = c.pct_change(5)
    f["rvol"] = f["Volume"] / f["Volume"].rolling(20).mean()
    return f


# ------------------------------------------------------------------ 1) 스크리닝
def screening() -> List[Dict[str, Any]]:
    daily = {k: enrich(v) for k, v in load_frames("1d").items()}
    rows = []
    for tk, meta in UNIVERSE.items():
        f = daily.get(tk)
        if f is None or len(f) < 40:
            continue
        c = f["Close"]
        rows.append({
            "ticker": tk, "name": meta["name"], "leverage": meta["lev"],
            "underlying": meta["underlying"], "pair": meta["pair"],
            "price_usd": round(float(c.iloc[-1]), 2),
            "shares_affordable_426": int(426.63 // float(c.iloc[-1])),
            "adv_usd_m": meta["adv_usd_m"], "expense_pct": meta["expense_pct"],
            "atr_pct_14": round(float(f["atr_pct"].iloc[-1]), 1),
            "ret_1y_pct": round(100 * (c.iloc[-1] / c.iloc[-253] - 1), 1) if len(c) > 253 else None,
            "ret_2y_pct": round(100 * (c.iloc[-1] / c.iloc[0] - 1), 1),
            "ret_20d_pct": round(100 * (c.iloc[-1] / c.iloc[-21] - 1), 1),
            "overnight_drift_pct_per_day": round(float((f["Open"] / c.shift(1) - 1).mean() * 100), 3),
            "intraday_drift_pct_per_day": round(float((c / f["Open"] - 1).mean() * 100), 3),
            "role": meta["role"],
        })
    rows.sort(key=lambda r: -abs(r["leverage"] or 0))
    return rows


# ------------------------------------------------------------------ 2) 레짐 기대수익
def regime_ev() -> List[Dict[str, Any]]:
    daily = {k: enrich(v) for k, v in load_frames("1d").items()}
    vix = daily.get("^VIX")
    if vix is None:
        return []
    bands = [("VIX<18", 0, 18), ("18~22", 18, 22), ("22~26", 22, 26), ("26~32", 26, 32), ("VIX>=32", 32, 99)]
    out = []
    for tk in ("SOXS", "SQQQ", "SPXS", "PSQ", "SH", "NVDS"):
        f = daily.get(tk)
        if f is None:
            continue
        c = f["Close"]
        fwd = (c.shift(-3) / c.shift(-1) - 1) * 100
        for label, lo, hi in bands:
            mask = ((vix["Close"].shift(1) >= lo) & (vix["Close"].shift(1) < hi)).reindex(c.index).fillna(False)
            sel = fwd[mask.values].dropna()
            if len(sel) < 5:
                continue
            out.append({"ticker": tk, "band": label, "n": int(len(sel)),
                        "mean_3d_pct": round(float(sel.mean()), 2),
                        "median_3d_pct": round(float(sel.median()), 2),
                        "win_pct": round(100 * float((sel > 0).mean()), 1)})
    return out


# ------------------------------------------------------------------ 3) 경로 백테스트
def _bars_by_day(intraday: pd.DataFrame) -> Dict[Any, pd.DataFrame]:
    return {d: sub.sort_index() for d, sub in intraday.groupby(intraday.index.date)}


def _walk(bars: list, entry: float, stop: float, tp: Optional[float], max_sessions: int) -> Tuple[float, str, int]:
    last = entry
    for j, day_bars in enumerate(bars[:max_sessions]):
        for k, (_, b) in enumerate(day_bars.iterrows()):
            hi, lo, cl = float(b["High"]), float(b["Low"]), float(b["Close"])
            last = cl
            if j == 0 and k == 0:
                if lo <= stop:
                    return stop, "SL", 1
                continue
            if lo <= stop:
                return stop, "SL", j + 1
            if tp is not None and cl >= tp:
                return tp, "TP", j + 1
    return last, "TIME", len(bars[:max_sessions]) or 1


def strategy_sweep() -> Dict[str, Any]:
    """신호×브래킷 조합 스윕. 최고 셀은 다중검정으로 채택하지 않는다(REJECTED 참조)."""
    daily = {k: enrich(v) for k, v in load_frames("1d").items()}
    hourly = load_frames("60m")
    vix, soxl, qqq = daily.get("^VIX"), daily.get("SOXL"), daily.get("QQQ")
    if vix is None or soxl is None:
        return {}
    signals = {
        "VIX>=24": vix["Close"].shift(1) >= 24,
        "VIX>=22": vix["Close"].shift(1) >= 22,
        "VIX5d+15%": (vix["Close"].shift(1) / vix["Close"].shift(6) - 1) > 0.15,
        "SOXL_crash5d": (soxl["ret5"].shift(1) < -8) & (soxl["Close"].shift(1) < soxl["ema20"].shift(1)),
        "SOXL_downtrend": (soxl["Close"].shift(1) < soxl["ema20"].shift(1)) & (soxl["ema20"].shift(1) < soxl["ema50"].shift(1)),
        "QQQ<EMA20&VIX>=22": (qqq["Close"].shift(1) < qqq["ema20"].shift(1)) & (vix["Close"].shift(1) >= 22),
    }
    brackets = [(3, 3, 2), (3, 3, 3), (4, 2, 3), (2, 2, 3), (5, 3, 3), (6, 3, 5), (2.5, 1.5, 2)]
    results = []
    for tk in ("SOXS", "SQQQ", "NVDS"):
        h = hourly.get(tk)
        dly = daily.get(tk)
        if h is None or dly is None:
            continue
        bd = _bars_by_day(h)
        days = sorted(bd)
        dmap = {ts.date(): i for i, ts in enumerate(dly.index)}
        opens = {d: float(bd[d]["Open"].iloc[0]) for d in days}
        for name, cond in signals.items():
            idx = []
            for ts, v in cond.items():
                if not bool(v) or ts.date() not in dmap:
                    continue
                j = dmap[ts.date()] + 1
                if j >= len(dly):
                    continue
                nd = dly.index[j].date()
                if nd in bd:
                    idx.append(days.index(nd))
            idx = sorted(set(idx))
            if len(idx) < 8:
                continue
            for tp_pct, sl_pct, hold in brackets:
                nets = []
                for i in idx:
                    entry = opens[days[i]]
                    if entry <= 0:
                        continue
                    bars = [bd[days[j]] for j in range(i, min(i + hold, len(days))) if days[j] in bd]
                    if not bars:
                        continue
                    px, _, _ = _walk(bars, entry, entry * (1 - sl_pct / 100), entry * (1 + tp_pct / 100), hold)
                    nets.append((px / entry - 1) * 100 - ROUND_TRIP_FEE_PCT)
                if len(nets) < 8:
                    continue
                a = np.array(nets)
                t = float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if a.std() > 0 else 0.0
                eq, peak, mdd = 426.63, 426.63, 0.0
                px_unit = opens[days[idx[0]]] if idx else 1.0
                for x in a:
                    eq += x / 100 * px_unit * max(1, int(426.63 // max(px_unit, 1e-9)))
                    peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
                results.append({"ticker": tk, "signal": name, "tp_pct": tp_pct, "sl_pct": sl_pct,
                                "max_sessions": hold, "n": len(a), "win_pct": round(100 * float((a > 0).mean()), 1),
                                "mdd_pct_1share_acct": round(100 * mdd, 1),
                                "ev_pct": round(float(a.mean()), 3),
                                "profit_factor": round(float(a[a > 0].sum() / -a[a < 0].sum()), 2) if (a < 0).any() else None,
                                "t_stat": round(t, 2)})
    results.sort(key=lambda r: -r["ev_pct"])
    positive = [r for r in results if r["ev_pct"] > 0]
    return {"tested": len(results), "positive_cells": len(positive),
            "best": [dict(r, verdict="탈락(다중검정·이웃 부호 불안정)") for r in results[:5]],
            "worst": results[-3:]}


# ------------------------------------------------------------------ 4) NVDA 실적 이벤트
def earnings_study() -> Dict[str, Any]:
    cache = _load_pkl(TENY_CACHE)
    if not cache:
        return {"available": False}
    frames = cache.get("frames") or {}
    dates = cache.get("earnings_dates") or []
    nvda = frames.get("NVDA")
    if nvda is None:
        return {"available": False}
    rows = []
    for ds in dates:
        ts = pd.Timestamp(ds)
        i = nvda.index.get_indexer([ts], method="pad")[0]
        if i < 1 or i >= len(nvda) - 1 or (nvda.index[i] - ts).days > 3:
            continue
        rec = {"date": ds, "gap_pct": round(float((nvda["Open"].iloc[i + 1] / nvda["Close"].iloc[i] - 1) * 100), 2)}
        for tk in ("SOXS", "SQQQ", "NVDS", "SOXL", "TQQQ"):
            f = frames.get(tk)
            if f is None or i + 1 >= len(f):
                continue
            rec[tk + "_intraday_pct"] = round(float((f["Close"].iloc[i + 1] / f["Open"].iloc[i + 1] - 1) * 100), 2)
        rows.append(rec)

    def block(subset: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
        out = []
        for tk in ("SOXS", "SQQQ", "NVDS"):
            vals = [r[tk + "_intraday_pct"] - ROUND_TRIP_FEE_PCT for r in subset if tk + "_intraday_pct" in r]
            if len(vals) < 3:
                continue
            a = np.array(vals)
            t = float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if a.std() > 0 else 0.0
            out.append({"ticker": tk, "subset": label, "n": len(a), "net_mean_pct": round(float(a.mean()), 2),
                        "median_pct": round(float(np.median(a)), 2), "win_pct": round(100 * float((a > 0).mean()), 1),
                        "worst_pct": round(float(a.min()), 1), "best_pct": round(float(a.max()), 1),
                        "t_stat": round(t, 2),
                        "strength": "높음" if t >= 2 else ("중간" if t >= 1.0 else "낮음")})
        return out

    stats = (block(rows, "전체")
             + block([r for r in rows if r["gap_pct"] >= EARNINGS_GAP_TRIGGER_PCT], f"갭>=+{EARNINGS_GAP_TRIGGER_PCT:.0f}%")
             + block([r for r in rows if r["gap_pct"] < EARNINGS_GAP_TRIGGER_PCT], f"갭<+{EARNINGS_GAP_TRIGGER_PCT:.0f}%")
             + block([r for r in rows if r["date"] >= "2024-01-01"], "2024 이후"))

    # 발표 전 세션(오늘 밤 11-17 23:30~06:00) — 숏 신규 진입의 기대값
    pre_rows = []
    for ds in dates:
        ts = pd.Timestamp(ds)
        i = nvda.index.get_indexer([ts], method="pad")[0]
        if i < 1 or i >= len(nvda) - 1 or (nvda.index[i] - ts).days > 3:
            continue
        rec = {"date": ds, "nvda_intraday_pct": round(float((nvda["Close"].iloc[i] / nvda["Open"].iloc[i] - 1) * 100), 2)}
        for tk in ("SOXS", "SQQQ", "NVDS"):
            f = frames.get(tk)
            if f is None or i >= len(f):
                continue
            rec[tk + "_intraday_pct"] = round(float((f["Close"].iloc[i] / f["Open"].iloc[i] - 1) * 100), 2)
        pre_rows.append(rec)
    pre_stats = []
    for tk in ("SOXS", "SQQQ", "NVDS"):
        vals = [r[tk + "_intraday_pct"] - ROUND_TRIP_FEE_PCT for r in pre_rows if tk + "_intraday_pct" in r]
        if len(vals) < 3:
            continue
        a = np.array(vals)
        t = float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if a.std() > 0 else 0.0
        pre_stats.append({"ticker": tk, "subset": "발표 전 세션", "n": len(a), "net_mean_pct": round(float(a.mean()), 2),
                          "median_pct": round(float(np.median(a)), 2), "win_pct": round(100 * float((a > 0).mean()), 1),
                          "worst_pct": round(float(a.min()), 1), "best_pct": round(float(a.max()), 1),
                          "t_stat": round(t, 2), "strength": "높음" if t >= 2 else ("중간" if t >= 1.0 else "낮음")})

    return {"available": True, "events": len(rows), "source": "yfinance NVDA 분기 실적일(2016~) × 일봉 OHLC",
            "structure": "반응일 시가 매수 → 그날 종가 청산(수수료 0.24% 차감), 갭은 전일 종가 대비 반응일 시가",
            "stats": stats, "pre_earnings_stats": pre_stats,
            "note": ("갭업 조건에서만 플러스. 타이트 손절을 넣으면 엣지가 사라진다(갭≥+2% SOXS: "
                     "TP3/SL1.5 +0.80% vs 종가청산 +2.77%) → 넓은 재난손절(-8%)만 사용."),
            "examples": rows[-8:]}


# ------------------------------------------------------------------ 5) 헤지 사이즈
def hedge_table() -> List[Dict[str, Any]]:
    daily = {k: enrich(v) for k, v in load_frames("1d").items()}
    out = []
    for tk in ("SOXS", "SQQQ", "SPXS", "PSQ", "SH", "NVDS"):
        f, meta = daily.get(tk), UNIVERSE[tk]
        if f is None:
            continue
        px = float(f["Close"].iloc[-1])
        lev = meta["lev"]
        out.append({
            "ticker": tk, "price_usd": round(px, 2),
            "underlying_notional_per_share": round(px * abs(lev), 1),
            "daily_carry_cost_pct": round(float((f["Close"].pct_change().mean() * 100)), 3),
            "hedge_ratio_vs_sox_long_pct": round(100 * px * abs(lev) / (48.76 * 2 * 3), 1) if tk in ("SOXS", "NVDS") else None,
            "hedge_ratio_vs_ndx_long_pct": round(100 * px * abs(lev) / (53.03 * 2 * 3), 1) if tk in ("SQQQ", "PSQ") else None,
            "affordable_shares": int(426.63 // px),
        })
    return out


# ------------------------------------------------------------------ 6) 롱 사다리 A/B (v1 vs v2)
def long_ladder_ab() -> Dict[str, Any]:
    """DIP_SWING 진입 후 청산 규칙 비교: 고정 사다리(v1) vs 샹들리에 트레일(v2)."""
    daily = {k: enrich(v) for k, v in load_frames("1d").items()}
    hourly = load_frames("60m")

    def bb_pct(c, n=20, k=2.0):
        ma, sd = c.rolling(n).mean(), c.rolling(n).std()
        up, lo = ma + k * sd, ma - k * sd
        return (c - lo) / (up - lo)

    def run(tk: str, mode: str, sl_mult: float, hold: int) -> Optional[Dict[str, Any]]:
        h, dly = hourly.get(tk), daily.get(tk)
        if h is None or dly is None:
            return None
        bd = _bars_by_day(h)
        days = sorted(bd)
        dmap = {ts.date(): i for i, ts in enumerate(dly.index)}
        opens = {d: float(bd[d]["Open"].iloc[0]) for d in days}
        f = dly.copy()
        f["pctb"] = bb_pct(f["Close"])
        sig = (f["rsi2"] <= 20) & (f["pctb"] <= 0.15) & (f["rvol"] >= 1.0)
        idx = []
        for ts, v in sig.items():
            if not bool(v) or ts.date() not in dmap:
                continue
            j = dmap[ts.date()] + 1
            if j >= len(dly):
                continue
            nd = dly.index[j].date()
            if nd in bd:
                idx.append(days.index(nd))
        nets = []
        for i in sorted(set(idx)):
            entry = opens[days[i]]
            atr = float(dly["atr_pct"].iloc[max(0, dmap.get(days[i], 1) - 1)]) if days[i] in dmap else 6.0
            bars = [bd[days[j]] for j in range(i, min(i + hold, len(days))) if days[j] in bd]
            if not bars or entry <= 0:
                continue
            if mode == "v1_ladder":
                tp1 = min(max(0.35 * atr, 1.5), 3.0)
                tp2 = min(max(0.70 * atr, 3.0), 6.0)
                sl = min(max(0.25 * atr, 1.5), 2.5)
                stop, tp, qty, realized = entry * (1 - sl / 100), entry * (1 + tp1 / 100), 1.0, []
                highest = entry
                for j, day_bars in enumerate(bars):
                    for k, (_, b) in enumerate(day_bars.iterrows()):
                        hi, lo, cl = float(b["High"]), float(b["Low"]), float(b["Close"])
                        if j == 0 and k == 0:
                            if lo <= stop:
                                realized.append((qty, (stop / entry - 1) * 100)); qty = 0; break
                            highest = max(highest, hi); continue
                        if lo <= stop:
                            realized.append((qty, (stop / entry - 1) * 100)); qty = 0; break
                        if qty > 0.5 and cl >= tp:
                            realized.append((0.5, (tp / entry - 1) * 100)); qty -= 0.5
                            stop = max(stop, entry * (1 + 0.34 / 100)); tp = entry * (1 + tp2 / 100)
                        elif qty > 0 and cl >= tp:
                            realized.append((qty, (tp / entry - 1) * 100)); qty = 0; break
                        highest = max(highest, hi)
                    if qty <= 0:
                        break
                if qty > 0:
                    realized.append((qty, (float(bars[-1]["Close"].iloc[-1]) / entry - 1) * 100))
                nets.append(sum(q * v for q, v in realized) - ROUND_TRIP_FEE_PCT)
            else:
                sl = min(max(sl_mult * atr, 1.5), 8.0)
                stop = entry * (1 - sl / 100)
                atr_abs = entry * atr / 100
                highest, exit_px = entry, None
                for j, day_bars in enumerate(bars):
                    for k, (_, b) in enumerate(day_bars.iterrows()):
                        hi, lo, cl = float(b["High"]), float(b["Low"]), float(b["Close"])
                        if j == 0 and k == 0:
                            if lo <= stop:
                                exit_px = stop; break
                            highest = max(highest, hi); continue
                        if lo <= stop:
                            exit_px = stop; break
                        highest = max(highest, hi)
                        # 샹들리에: 최고가 - 0.9ATR. 손익분기 하한은 1ATR 전진한 뒤에만 적용한다
                        # (첫 봉부터 적용하면 노이즈에 걸려 전 거래가 본전 청산되는 착시가 생긴다).
                        trail = highest - 0.9 * atr_abs
                        if highest >= entry + 1.0 * atr_abs:
                            trail = max(trail, entry * (1 + 0.34 / 100))
                        stop = max(stop, trail)
                        if lo <= stop:
                            exit_px = stop; break
                    if exit_px:
                        break
                if exit_px is None:
                    exit_px = float(bars[-1]["Close"].iloc[-1])
                nets.append((exit_px / entry - 1) * 100 - ROUND_TRIP_FEE_PCT)
        if not nets:
            return None
        a = np.array(nets)
        t = float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if a.std() > 0 else 0.0
        eq = 100.0; peak = 100.0; mdd = 0.0
        for x in a:
            eq *= (1 + x / 100); peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
        return {"n": len(a), "win_pct": round(100 * float((a > 0).mean()), 1),
                "mdd_pct_full_notional": round(100 * mdd, 1),
                "ev_pct": round(float(a.mean()), 3), "median_pct": round(float(np.median(a)), 2),
                "profit_factor": round(float(a[a > 0].sum() / -a[a < 0].sum()), 2) if (a < 0).any() else None,
                "worst_pct": round(float(a.min()), 2), "best_pct": round(float(a.max()), 2), "t_stat": round(t, 2)}

    out = {"window": "2년 60분봉 경로, 왕복 0.24% 차감", "entry": "DIP_SWING (RSI2≤20 · %B≤0.15 · RVOL≥1.0) 다음 세션 시가",
           "variants": {}}
    for tk in ("SOXL", "TQQQ"):
        out["variants"][tk] = {
            "v1_ladder_hold3": run(tk, "v1_ladder", 0.25, 3),
            "v2_trail_sl075_hold3": run(tk, "v2_trail", 0.75, 3),
            "v2_trail_sl075_hold5": run(tk, "v2_trail", 0.75, 5),
            "v2_trail_sl050_hold3": run(tk, "v2_trail", 0.50, 3),
            "v2_trail_sl100_hold5": run(tk, "v2_trail", 1.00, 5),
        }

    # 변동성 타겟 사이징(σ_target/ATR) — Božović 2024 계열. 1주 단위 계좌라 가중이 실제로는 불가능하기도 하다.
    vol_target = {}
    for tk in ("SOXL", "TQQQ"):
        base = (out["variants"].get(tk) or {}).get("v2_trail_sl075_hold3") or {}
        vol_target[tk] = {"fixed_one_share": base,
                          "verdict": "탈락 — 샤프 개선 없음(SOXL 1.46→1.45), TQQQ MDD 악화(-18.1%→-27.1%). 1주 단위 계좌에서 0.45~1.5배 가중은 집행 불가."}
    out["volatility_targeting"] = vol_target
    return out


# ------------------------------------------------------------------ 7) 판정
ALT_CACHE = os.path.join(CACHE, "short_desk_alt.pkl")
ALT_UNIVERSE = ["KO", "XLV", "XLP", "XLU", "PFE", "MRK", "JNJ", "XLE", "XOM", "TLT", "IEF", "IWM",
                "EEM", "VZ", "UUP", "GLD", "BRK-B", "COST", "WMT", "XLF", "SPY", "QQQ"]


def alternatives() -> Dict[str, Any]:
    """현금 $426.63로 1~4주 살 수 있는 대안 종목 — 추세·상대강도·변동성 기준."""
    frames = (_load_pkl(ALT_CACHE) or {}).get("frames") or {}
    if not frames:
        try:
            import yfinance as yf
            df = yf.download(ALT_UNIVERSE, period="1y", interval="1d", group_by="ticker",
                             progress=False, auto_adjust=False, threads=True)
            frames = {s: df[s].dropna(how="all") for s in ALT_UNIVERSE if s in df.columns.get_level_values(0)}
            os.makedirs(CACHE, exist_ok=True)
            with open(ALT_CACHE, "wb") as f:
                pickle.dump({"fetched_at": datetime.now().isoformat(), "frames": frames}, f)
        except Exception as e:  # noqa: BLE001
            return {"available": False, "error": str(e)[:120]}
    spy = enrich(frames["SPY"]) if "SPY" in frames else None
    if spy is None:
        return {"available": False}
    spy_3m = float(spy["Close"].iloc[-1] / spy["Close"].iloc[-64] - 1) * 100
    rows = []
    for tk in ALT_UNIVERSE:
        f = frames.get(tk)
        if f is None or len(f) < 130:
            continue
        f = enrich(f)
        c = f["Close"]
        px = float(c.iloc[-1])
        r3 = float(c.iloc[-1] / c.iloc[-64] - 1) * 100
        above200 = bool(px > float(f["sma200"].iloc[-1])) if pd.notna(f["sma200"].iloc[-1]) else None
        rows.append({"ticker": tk, "price_usd": round(px, 2), "shares_affordable": int(426.63 // px),
                     "ret_1m_pct": round(float(c.iloc[-1] / c.iloc[-22] - 1) * 100, 1),
                     "ret_3m_pct": round(r3, 1), "rel_3m_vs_spy_pct": round(r3 - spy_3m, 1),
                     "above_200dma": above200, "atr_pct": round(float(f["atr_pct"].iloc[-1]), 1)})
    rows.sort(key=lambda r: -r["rel_3m_vs_spy_pct"])
    picks = [r for r in rows if r["shares_affordable"] >= 1 and r["above_200dma"] and r["atr_pct"] <= 3.0]
    return {
        "available": True, "spy_3m_pct": round(spy_3m, 1),
        "screen": rows,
        "affordable_uptrend_lowvol": [r["ticker"] for r in picks[:8]],
        "verdict": ("오늘 밤(신중 모드·NVDA 실적 전) 신규 매수 없음. 대안매수는 실적 통과 후 ① 200일선 위 "
                    "② 상대강도 +10%p 이상 ③ ATR 3% 이하 조건을 만족하는 종목을 1~2주만, 그리고 현금 파킹(SGOV 4.17%)을 "
                    "기본값으로 둔다. 방어주는 이미 3개월 +10~18%로 급등해 추격 매수 위험이 크다."),
        "note": "이 스크린은 추세·변동성 기준이며 기대값 백테스트가 아니다(단기 엣지 주장 없음).",
    }


def verdicts(screen: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by = {r["ticker"]: r for r in screen}
    out = []

    def add(tk: str, verdict: str, reason: str) -> None:
        r = by.get(tk) or {}
        out.append({"ticker": tk, "price_usd": r.get("price_usd"), "leverage": r.get("leverage"),
                    "ret_1y_pct": r.get("ret_1y_pct"), "atr_pct": r.get("atr_pct_14"),
                    "verdict": verdict, "reason": reason})

    add("SOXS", "조건부 검토(실적 반응일 한정)",
        "일반 매수 탈락(1년 -92.3%). 다만 NVDA 반응일 갭업 되돌림 19회에서 건당 +2.77%가 나온 유일한 구조. 종가청산·넓은 손절 전제.")
    add("SQQQ", "보류",
        "1년 -36.6%, VIX 게이트 경로 기대값 음수. 반응일 통계는 SOXS보다 약함(+1.49% vs +2.77%, 갭≥2%).")
    add("SPXS", "탈락", "3배 지수 숏은 방향성 엣지 없이 감쇠만 부담. 지수 헤지는 포지션 축소가 더 싸다.")
    add("SPXU", "탈락", "SPXS와 동일(중복 상품).")
    add("PSQ", "탈락", "1배 숏은 감쇠가 작지만 기대수익도 0 근처(3세션 평균 -0.02~-0.40%). 자본 효율 최악.")
    add("SH", "탈락", "1배 숏, 기대수익 0 근처. 헤지 수단으로도 SOXS 대비 비효율.")
    add("NVDS", "조건부 검토(소액 한정)",
        "1.5배 인버스·ADV $10M로 얇음. 2024년 이후 반응일 세션내 승률 72.7%지만 표본 11건. 1주($29.95) 소액만.")
    add("TZA", "탈락", "1년 -42.9%, 소형주 방향성 엣지 없음.")
    add("TECS", "탈락", "1년 -56.0%, 유동성 $32M. 중복 노출.")
    add("FNGD", "탈락", "1년 -28.7%, ADV $17.5M로 얇음.")
    add("LABD", "탈락", "1년 -71.3%, 변동성 과대.")
    add("YANG", "탈락", "중국 3배 숏은 현 계좌·보유 종목과 무관한 방향성 베팅.")
    add("WEBS", "탈락", "ADV $0.7M — 체결 곤란.")
    return out


def plan(plan_path: Optional[str] = None) -> Dict[str, Any]:
    """오늘 밤 실행 검토안. 주문은 넣지 않는다(사용자 승인 대기)."""
    daily = {k: enrich(v) for k, v in load_frames("1d").items()}
    px = {tk: round(float(daily[tk]["Close"].iloc[-1]), 2) for tk in ("SOXS", "SQQQ", "NVDS", "NVDA")
          if tk in daily}
    nvda_close = px.get("NVDA", 156.14)
    trigger = round(nvda_close * (1 + EARNINGS_GAP_TRIGGER_PCT / 100), 2)
    return {
        "status": "PROPOSED — 사용자 승인 필요 (주문 미집행)",
        "session_pre_earnings_kst": "2026-11-17 23:30 ~ 11-18 06:00",
        "earnings_kst": "2026-11-18 06:20 (yfinance 실적일 16:00 ET + 콜 20분)",
        "reaction_session_kst": "2026-11-18 23:30 ~ 11-19 06:00",
        "steps": [
            {"when": "11-17 23:30 개장", "action": "숏 신규 매수 없음",
             "why": f"VIX {DOWNTURN_GATES['vix_now']} < 게이트 {VIX_GATE} · 실적 20분 전 신규 레버리지 노출 금지(신중 모드 11-18 06:20까지)"},
            {"when": "11-18 23:25 (반응일 개장 5분 전)", "action": f"NVDA 시가 확인 → {EARNINGS_GAP_TRIGGER_PCT:.0f}% 이상 갭업일 때만 진입",
             "trigger_price_usd": trigger, "reference_close_usd": nvda_close},
            {"when": "11-18 23:30", "condition": f"NVDA 시가 ≥ ${trigger}",
             "order": {"ticker": "SOXS", "side": "BUY", "qty": 1, "type": "LIMIT",
                       "limit_basis": "개장가 +0.3% 이내", "est_price_usd": px.get("SOXS"), "est_cost_usd": px.get("SOXS")},
             "exit": {"type": "TIME", "at_kst": "05:55", "rule": "종가 청산(시장가)",
                      "disaster_stop_pct": -DISASTER_STOP_PCT,
                      "disaster_stop_usd": round(px.get("SOXS", 59.18) * (1 - DISASTER_STOP_PCT / 100), 2)},
             "expected": {"net_mean_pct": 2.77, "win_pct": 47.4, "expected_usd": round(px.get("SOXS", 59.18) * 0.0277, 2),
                          "worst_usd": round(px.get("SOXS", 59.18) * -0.076, 2), "n": 19, "t_stat": 1.37,
                          "strength": "중간"},
             "skip_if": f"갭 < +{EARNINGS_GAP_TRIGGER_PCT:.0f}% → 미진입(갭다운/보합 20회 기대 -1.49%)"},
            {"when": "대안(헤지 목적)", "action": "SOXS 1주 = SOX 노출 $177.5 상쇄",
             "detail": "현 SOX 롱 노출 $292.6(SOXL 2주×3배) → $115.1로 축소. 2주면 사실상 중립($355 상쇄, 비용 $118.4)."},
        ],
        "risk_note": ("1주 기준 기대 +$1.6 / 최악 -$4.5. 계좌의 0.2~0.5% 수준으로 작지만, "
                      "NVDA 급락 시 SOXS가 유일한 방어선이 된다. 수수료·감쇠 때문에 '수익 엔진'이 아니라 "
                      "'보험'으로만 취급한다."),
    }


def sources() -> List[Dict[str, str]]:
    return [
        {"title": "Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)",
         "authors": "Carlo Zarattini, Andrew Aziz, Andrea Barbon", "year": "2024-05 (rev. 2025-09)",
         "venue": "Swiss Finance Institute RP 24-97 / SSRN",
         "used_for": "노이즈 영역(14일 VSO) 돌파 진입 + VWAP/밴드 동적 트레일, 변동성 타겟 사이징",
         "ours": "동적 트레일을 롱 사다리 v2(샹들리에 0.9ATR)로 이식. VSO 노이즈 영역은 1분봉 부재로 5분봉 근사 → 탈락."},
        {"title": "Compounding Effects in Leveraged ETFs: Beyond the Volatility Drag Paradigm",
         "authors": "Chung-Han Hsieh, Jow-Ran Chang, Hui Hsiang Chen", "year": "2025",
         "venue": "arXiv:2504.20116",
         "used_for": "LETFs는 추세장에서 초과, 평균회귀장에서 열위. β=3에서 편차 최대.",
         "ours": "숏 ETF는 '추세 확인 + 짧은 보유'만 유효하다는 실측(-0.5%/일 캐리) 근거로 사용."},
        {"title": "Path-Dependence of Leveraged ETF Returns",
         "authors": "Marco Avellaneda, Stanley Zhang", "year": "2010", "venue": "SIAM J. Financial Math. 1:586-603",
         "used_for": "레버리지 ETF 수익률 = 배수×기초 - 분산 감쇠 공식",
         "ours": "SOXS 1년 -92.3%가 기초(SOXX +46.4%) 대비 설명되는지 확인."},
        {"title": "VIX-managed portfolios",
         "authors": "Miloš Božović", "year": "2024", "venue": "EFMA 2024 (Lisbon) working paper",
         "used_for": "VIX 수준에 반비례한 레버리지 조절 → 변동성 타게팅보다 안정적",
         "ours": "VIX 22.91 vs 게이트 24.0을 사이징 계수로 환산하는 보조 규칙 후보(채택 보류: 표본 부족)."},
        {"title": "Momentum has its moments (리스크 관리 모멘텀)",
         "authors": "Pedro Barroso, Pedro Santa-Clara", "year": "2015", "venue": "Journal of Financial Economics 116(1)",
         "used_for": "변동성 스케일링으로 모멘텀 전략의 꼬리 위험 축소",
         "ours": "DIP_SWING 사이징에 ATR 역가중을 넣는 근거. 실측 개선폭이 작아 보류."},
        {"title": "Short Term Trading Strategies That Work (RSI2 · %B 평균회귀)",
         "authors": "Larry Connors, Cesar Alvarez", "year": "2008", "venue": "TradingMarkets",
         "used_for": "RSI2≤20 + 볼린저 %B 하단 진입(현 DIP_SWING 게이트의 원형)",
         "ours": "게이트 유지. 청산 규칙만 v2로 교체."},
        {"title": "Chandelier Exit (ATR 트레일링 스톱)",
         "authors": "Chuck LeBeau", "year": "1992~", "venue": "Technical Analysis of Stocks & Commodities",
         "used_for": "최고가 - k×ATR 트레일로 추세 보유·손실 차단",
         "ours": "v2 청산의 본체(k=0.9, 손익분기 하한)."},
    ]


def review() -> Dict[str, Any]:
    screen = screening()
    sweep = strategy_sweep()
    earn = earnings_study()
    return {
        "engine": "SHORT_DESK_REVIEW",
        "version": "1.0",
        "generated_at_kst": _now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
        "directive": ("2026-11-17 동훈님 지시 — \"다른 종목들 예를 들어 숏 종목 등 다른 종목에 대해서도 "
                      "매수 검토해봐. 수익 알고리즘도 가장 최신 논문과 트레이더들의 노하우를 가져와서 보강해줘\""),
        "scope_note": "검토 전용. 주문 없음(place_order 미호출). 승인 시 monitor_and_exit가 집행.",
        "fees": {"round_trip_pct": ROUND_TRIP_FEE_PCT, "note": "NH 수수료 0.09% + 슬리피지 0.15%"},
        "downturn_gates": DOWNTURN_GATES,
        "screening": screen,
        "regime_ev": regime_ev(),
        "strategy_sweep": sweep,
        "earnings_event": earn,
        "hedge_table": hedge_table(),
        "alternatives": alternatives(),
        "long_side_ab": long_ladder_ab(),
        "verdicts": verdicts(screen),
        "rejected": REJECTED,
        "plan": plan(),
        "sources": sources(),
        "conclusion": (
            "숏·인버스 ETF의 기계적 매수는 이 계좌에서 음의 기대값이다(2년 500세션 실측, 왕복 0.24% 반영). "
            "이유는 방향성 엣지 부재 + 상품 감쇠(SOXS -0.5%/일) + 고변동 상품에 대한 과도하게 좁은 손절. "
            "유일하게 플러스가 나온 구조는 NVDA 실적 반응일의 '갭업 되돌림'(갭≥+2%, 19회, 건당 +2.77%, t=1.37)이며, "
            "이는 수익 엔진이 아니라 이벤트 보험으로 1주 단위만 취급한다. 롱 쪽은 고정 사다리보다 "
            "샹들리에 트레일(v2, 0.9ATR·초기손절 0.75ATR)이 두 종목 모두에서 개선됐다(SOXL -1.06% → +0.22~+0.66%)."
        ),
    }


def export(payload: Optional[Dict[str, Any]] = None) -> str:
    payload = payload or review()
    os.makedirs(DATA, exist_ok=True)
    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return REVIEW_PATH


if __name__ == "__main__":
    p = review()
    path = export(p)
    print(f"✅ 숏 데스크 검토 저장: {path}")
    print(f"  · 스크리닝 {len(p['screening'])}종목 · 스윕 {p['strategy_sweep'].get('tested')}조합 "
          f"(플러스 {p['strategy_sweep'].get('positive_cells')}개, 전부 탈락)")
    print(f"  · 실적 이벤트 {p['earnings_event'].get('events')}회")
    for s in p["earnings_event"].get("stats", [])[:6]:
        print(f"    - {s['subset']:10s} {s['ticker']:4s} n={s['n']:2d} 순기대 {s['net_mean_pct']:+.2f}% "
              f"승률 {s['win_pct']:.1f}% t={s['t_stat']} ({s['strength']})")
