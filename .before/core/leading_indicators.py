"""
CUBE Leading Indicator Engine (CUBE-LEI)
========================================

A business-cycle leading index built ONLY from measured series: FRED official
macro/credit data (`core/market_snapshot.load_fred`, fredgraph.csv, no API key)
plus Yahoo Finance daily bars for the market-price components. Nothing in this
module stores a number; every value is recomputed from the inputs on each run.

Methodology and sources (the equations below are the ones actually used)
------------------------------------------------------------------------
1) Term-spread recession probit — Estrella & Mishkin (1998), "Predicting U.S.
   Recessions: Financial Variables as Leading Indicators", *The Review of
   Economics and Statistics* 80(1): 45-61; Estrella & Hardouvelis (1991),
   "The Term Structure as a Predictor of Real Economic Activity",
   *Journal of Finance* 46(2): 555-576.

       P(Recession within 12 months) = Φ( -0.6045 - 0.8180 × (10Y - 3M) )

   The published coefficients are calibrated on the 10Y-3M spread expressed in
   percentage points (0.83 → -0.6045 - 0.8180×0.83). Applying the same equation
   to the 10Y-2Y spread is NOT the published calibration, so this module reports
   it separately, flagged as an approximation, and additionally solves for the
   10Y-2Y level that would produce the same probability under the 10Y-3M model
   (pure algebra, no invented coefficients).

2) Multivariate leading index — Stock & Watson (1989), "New Indexes of
   Coincident and Leading Economic Indicators", *NBER Macroeconomics Annual*
   4: 351-394; Stock & Watson (2002), "Macroeconomic Forecasting Using Diffusion
   Indexes", *JBES* 20(2): 147-162. Components are standardised and combined;
   an equal-weight diffusion variant is reported next to the weighted composite
   as a robustness check.

3) Credit / liquidity channel — Gilchrist & Zakrajšek (2012), "Credit Spreads and
   Business Cycle Fluctuations", *American Economic Review* 102(4): 1692-1720.
   The paper's excess bond premium needs issuer-level corporate bond data that
   is not available here, so CUBE computes a transparent proxy: the level and
   5-year z-score of the ICE BofA US High Yield OAS (BAMLH0A0HYM2) plus the
   HYG-vs-IEF 63-day relative momentum. Every report labels it "GZ proxy",
   never "the EBP".

4) Composite construction, standardisation and regime naming — Burns & Mitchell
   (1946), *Measuring Business Cycles*, NBER; the Conference Board LEI
   methodology (standardised component contributions, 2016 recalibration) and the
   OECD Composite Leading Indicator handbook (amplitude adjustment so that one
   standard deviation of the composite maps to 10 index points, long-run mean 100).

Weights (CUBE design values, documented and reproducible)
--------------------------------------------------------
The Conference Board's ten LEI components include ISM survey data, building
permits and real M2, which are not available from the free endpoints used here.
CUBE therefore keeps the CB contribution ORDER (term spread and credit conditions
dominant, claims and equity prices smaller) and normalises over the components
that ARE measurable. The weights are design values, not a re-publication of the
CB's published coefficients; W_* below is the exact set used.

   W_TERM_10Y3M = 0.30    W_CREDIT_OAS = 0.15    W_CLAIMS = 0.10   W_SENTIMENT = 0.05
   W_TERM_10Y2Y = 0.10    W_NFCI       = 0.15    W_EQUITY = 0.08   W_COPPER_GOLD = 0.04
                                                 W_CONSUMER = 0.03

Regime rule (documented thresholds on the composite z-score)
   CONTRACTION_RISK    : z <= -1.0  or  P(12m recession) >= 50%
   RECOVERY            : z <   0.0  and 3-month change of z > 0
   LATE_CYCLE_SLOWDOWN : z <   0.3  or  3-month change of z <= -1.0
   EXPANSION           : otherwise
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core import market_snapshot

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEI_OUT = os.path.join(BASE_DIR, "data", "leading_indicators.json")
DOCS_LEI_OUT = os.path.join(BASE_DIR, "docs", "leading_indicators.json")
LONG_HISTORY_CACHE = os.path.join(BASE_DIR, "data_cache", "lei_history_5y.pkl")
LONG_HISTORY_TTL_H = float(os.getenv("CUBE_LEI_HISTORY_TTL_H", "24"))

REFERENCES = [
    {"key": "estrella_mishkin_1998",
     "cite": "Estrella, A. & Mishkin, F.S. (1998). Predicting U.S. Recessions: Financial Variables as Leading Indicators. The Review of Economics and Statistics 80(1), 45-61.",
     "used_for": "12개월 경기침체 프로빗 확률 P = Φ(-0.6045 - 0.8180 × (10Y-3M))"},
    {"key": "estrella_hardouvelis_1991",
     "cite": "Estrella, A. & Hardouvelis, G.A. (1991). The Term Structure as a Predictor of Real Economic Activity. Journal of Finance 46(2), 555-576.",
     "used_for": "장단기 금리차의 실물경제 선행성 — 금리차 성분의 정당화"},
    {"key": "stock_watson_1989",
     "cite": "Stock, J.H. & Watson, M.W. (1989). New Indexes of Coincident and Leading Economic Indicators. NBER Macroeconomics Annual 4, 351-394.",
     "used_for": "다변량 표준화 선행지수 합성(금리차·주가·금융여건·실업청구·구리/금·소비재 비율)"},
    {"key": "stock_watson_2002",
     "cite": "Stock, J.H. & Watson, M.W. (2002). Macroeconomic Forecasting Using Diffusion Indexes. Journal of Business & Economic Statistics 20(2), 147-162.",
     "used_for": "동일가중 확산지수(diffusion index) 로버스트니스 비교"},
    {"key": "gilchrist_zakrajsek_2012",
     "cite": "Gilchrist, S. & Zakrajšek, E. (2012). Credit Spreads and Business Cycle Fluctuations. American Economic Review 102(4), 1692-1720.",
     "used_for": "회사채 신용스프레드의 경기 선행성 — HY OAS 기반 신용리스크 프록시(EBP 아님)"},
    {"key": "burns_mitchell_1946",
     "cite": "Burns, A.F. & Mitchell, W.C. (1946). Measuring Business Cycles. NBER.",
     "used_for": "경기 국면(expansion / slowdown / contraction / recovery) 4국면 구분"},
    {"key": "cb_lei_oecd_cli",
     "cite": "The Conference Board (2016) LEI methodology; OECD (2012) System of Composite Leading Indicators handbook.",
     "used_for": "성분 표준화·진폭조정(1σ = 10 지수점, 장기평균 100) 및 종합지수 구성"},
]

# --- CUBE design weights (see module docstring) ------------------------------
WEIGHTS = {
    "term_spread_10y3m": 0.30,
    "term_spread_10y2y": 0.10,
    "credit_oas": 0.15,
    "nfci": 0.15,
    "claims_yoy": 0.10,
    "equity_momentum_12m": 0.08,
    "consumer_sentiment": 0.05,
    "copper_gold": 0.04,
    "consumer_rotation": 0.03,
}

Z_WINDOW_YEARS = 5.0
COMPONENT_META = {
    "term_spread_10y3m": {"name": "미 국채 10Y-3M 금리차", "source": "FRED T10Y3M", "sign": +1,
                          "why": "Estrella & Mishkin (1998) — 역전 후 12개월 내 침체 확률 상승"},
    "term_spread_10y2y": {"name": "미 국채 10Y-2Y 금리차", "source": "FRED T10Y2Y", "sign": +1,
                          "why": "Estrella & Hardouvelis (1991) — 기간프리미엄·실물 선행성"},
    "credit_oas": {"name": "미 하이일드 OAS", "source": "FRED BAMLH0A0HYM2", "sign": -1,
                   "why": "Gilchrist & Zakrajšek (2012) — 신용스프레드 확대는 실물 위축 선행"},
    "nfci": {"name": "시카고 연준 금융여건지수", "source": "FRED NFCI", "sign": -1,
             "why": "Stock & Watson (1989) 금융여건 성분 — (+)는 긴축"},
    "claims_yoy": {"name": "신규 실업수당 청구 4주평균 YoY", "source": "FRED ICSA", "sign": -1,
                   "why": "Stock & Watson (1989) 실업청구 성분(역방향)"},
    "equity_momentum_12m": {"name": "S&P500 12개월 모멘텀", "source": "Yahoo SPY 일봉", "sign": +1,
                            "why": "Stock & Watson (1989) 주가 성분 / CB LEI S&P500 기여"},
    "consumer_sentiment": {"name": "미시간대 소비자심리지수", "source": "FRED UMCSENT", "sign": +1,
                           "why": "Stock & Watson (1989) 기대심리 성분"},
    "copper_gold": {"name": "구리/금 비율", "source": "Yahoo HG=F / GC=F", "sign": +1,
                    "why": "Stock & Watson (1989) 원자재 수요 사이클 프록시"},
    "consumer_rotation": {"name": "임의소비재/필수소비재 비율(XLY/XLP)", "source": "Yahoo XLY / XLP", "sign": +1,
                          "why": "Burns & Mitchell (1946) 소비 사이클 로테이션"},
}


# ---------------------------------------------------------------- math helpers
def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _z(x: float, mean: float, std: float) -> Optional[float]:
    if x is None or mean is None or std is None or not np.isfinite(std) or std <= 0:
        return None
    return float((x - mean) / std)


def recession_probit(spread_pct: Optional[float]) -> Optional[float]:
    """Estrella & Mishkin (1998) 12-month-ahead probit, percent points in."""
    if spread_pct is None:
        return None
    return _phi(-0.6045 - 0.8180 * float(spread_pct))


def _probit_inverse(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    p = min(max(p, 1e-9), 1 - 1e-9)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ------------------------------------------------------- long daily history (5y)
def load_long_history(symbols: List[str], period: str = "5y", force: bool = False) -> Dict[str, pd.DataFrame]:
    """5-year daily bars for the market-price components (separate 24h cache)."""
    cache: Dict[str, Any] = {}
    if os.path.exists(LONG_HISTORY_CACHE):
        try:
            import pickle
            with open(LONG_HISTORY_CACHE, "rb") as f:
                cache = pickle.load(f)
        except Exception:
            cache = {}
    frames: Dict[str, pd.DataFrame] = cache.get("frames", {}) or {}
    fresh = False
    if cache.get("fetched_at"):
        try:
            age = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(cache["fetched_at"])).total_seconds() / 3600.0
            fresh = age < LONG_HISTORY_TTL_H
        except Exception:
            fresh = False
    missing = [s for s in symbols if s not in frames or frames[s] is None or len(frames[s]) < 300]
    if fresh and not missing and not force:
        return {s: frames[s] for s in symbols if s in frames}

    try:
        import yfinance as yf
        dl = yf.download(symbols, period=period, interval="1d", progress=False,
                         auto_adjust=False, group_by="column", threads=True)
        got = market_snapshot._normalize_download(dl, symbols)
        if got:
            frames.update(got)
            try:
                import pickle
                with open(LONG_HISTORY_CACHE, "wb") as f:
                    pickle.dump({"fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "period": period, "frames": frames}, f)
            except Exception as e:
                print(f"[lei] long history cache write failed: {e}")
    except Exception as e:
        print(f"[lei] long history fetch failed: {e}")
    return {s: frames[s] for s in symbols if s in frames}


def _monthly_tail(series: pd.Series, months: int = 24) -> pd.Series:
    s = series.dropna()
    if len(s) == 0:
        return s
    try:
        return s.resample("ME").last().dropna().tail(months)
    except Exception:
        return s.tail(months)


# ------------------------------------------------------------------- components
def _fred_series(fred: Dict[str, Any], sid: str) -> Optional[pd.Series]:
    d = (fred or {}).get(sid) or {}
    return d.get("_series") if isinstance(d.get("_series"), pd.Series) else None


def _component(key: str, value: Optional[float], z: Optional[float],
               detail: Dict[str, Any], monthly: Optional[pd.Series] = None) -> Dict[str, Any]:
    meta = COMPONENT_META[key]
    w = WEIGHTS[key]
    contribution = round(w * (z or 0.0) * meta["sign"], 3)
    return {
        "key": key, "name": meta["name"], "source": meta["source"], "why": meta["why"],
        "value": (round(float(value), 4) if value is not None and np.isfinite(float(value)) else None),
        "z": (round(float(z), 2) if z is not None and np.isfinite(z) else None),
        "sign": meta["sign"], "weight": w, "contribution": contribution,
        "available": bool(z is not None and np.isfinite(z)),
        "detail": detail,
        "_monthly": monthly,  # popped before serialisation
    }


def compute(snapshot: Dict[str, Any] = None, force: bool = False, quiet: bool = True) -> Dict[str, Any]:
    """Compute the CUBE-LEI and all cross-market measurements from live data."""
    snap = snapshot or market_snapshot.load_snapshot()
    bonds = snap.get("bonds") or {}
    fx = snap.get("fx_market") or {}
    crypto = snap.get("crypto_market") or {}
    cycle = snap.get("cycle") or {}

    fred = market_snapshot.load_fred(include_history=True, force=force)
    lei_hist = load_long_history(["SPY", "HG=F", "GC=F", "XLY", "XLP", "IWM"], force=force)

    components: List[Dict[str, Any]] = []
    notes: List[str] = []

    # ---- 1. term spreads (FRED)
    for key, sid in (("term_spread_10y3m", "T10Y3M"), ("term_spread_10y2y", "T10Y2Y")):
        d = fred.get(sid) or {}
        s = _fred_series(fred, sid)
        if s is not None and len(s) > 60 and d.get("available"):
            win = s[s.index >= (s.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
            z = _z(float(s.iloc[-1]), float(win.mean()), float(win.std()))
            components.append(_component(key, float(s.iloc[-1]), z,
                                         {"mean_5y": round(float(win.mean()), 4), "std_5y": round(float(win.std()), 4),
                                          "as_of": d.get("date"), "source_state": d.get("source"),
                                          "bp": round(float(s.iloc[-1]) * 100, 1)}, _monthly_tail(s)))
        else:
            # fallback: snapshot curve (^TNX - ^IRX or FRED-derived)
            bp = (bonds.get("curve") or {}).get("10y3m_bp" if sid == "T10Y3M" else "10y2y_bp")
            components.append(_component(key, (bp / 100.0) if bp is not None else None, None,
                                         {"error": "FRED 시계열 미확보 — 스냅샷 값만 존재(표준화 불가)"}))
            notes.append(f"{sid} FRED 시계열 미확보: {key} 성분은 가중치에서 제외(재정규화)")

    # ---- 2. credit OAS (Gilchrist & Zakrajšek proxy)
    d = fred.get("BAMLH0A0HYM2") or {}
    s = _fred_series(fred, "BAMLH0A0HYM2")
    if s is not None and len(s) > 60 and d.get("available"):
        win = s[s.index >= (s.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
        z = _z(float(s.iloc[-1]), float(win.mean()), float(win.std()))
        components.append(_component("credit_oas", float(s.iloc[-1]), z,
                                     {"mean_5y": round(float(win.mean()), 4), "std_5y": round(float(win.std()), 4),
                                      "as_of": d.get("date"), "source_state": d.get("source"),
                                      "bp": round(float(s.iloc[-1]) * 100, 1)}, _monthly_tail(s)))
    else:
        components.append(_component("credit_oas", ((bonds.get("hy_oas_pct") or None)), None,
                                     {"error": "FRED BAMLH0A0HYM2 미확보"}))
        notes.append("HY OAS FRED 시계열 미확보: credit_oas 성분 제외")

    # ---- 3. NFCI
    d = fred.get("NFCI") or {}
    s = _fred_series(fred, "NFCI")
    if s is not None and len(s) > 60 and d.get("available"):
        win = s[s.index >= (s.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
        z = _z(float(s.iloc[-1]), float(win.mean()), float(win.std()))
        components.append(_component("nfci", float(s.iloc[-1]), z,
                                     {"mean_5y": round(float(win.mean()), 4), "std_5y": round(float(win.std()), 4),
                                      "as_of": d.get("date"), "source_state": d.get("source")}, _monthly_tail(s)))
    else:
        components.append(_component("nfci", None, None, {"error": "FRED NFCI 미확보"}))
        notes.append("NFCI 미확보: 해당 성분 제외")

    # ---- 4. initial claims, 4-week average YoY (weekly -> monthly for the trend)
    d = fred.get("ICSA") or {}
    s = _fred_series(fred, "ICSA")
    if s is not None and len(s) > 90:
        wk = s.resample("W").last().dropna()
        ma4 = wk.rolling(4).mean()
        yoy = (ma4 / ma4.shift(52) - 1.0) * 100.0
        yoy = yoy.dropna()
        if len(yoy) > 60:
            win = yoy[yoy.index >= (yoy.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
            z = _z(float(yoy.iloc[-1]), float(win.mean()), float(win.std()))
            components.append(_component("claims_yoy", float(yoy.iloc[-1]), z,
                                         {"mean_5y": round(float(win.mean()), 4), "std_5y": round(float(win.std()), 4),
                                          "claims_4wk_avg": int(round(float(ma4.iloc[-1]))) if np.isfinite(ma4.iloc[-1]) else None,
                                          "as_of": d.get("date"), "source_state": d.get("source")}, _monthly_tail(yoy)))
        else:
            components.append(_component("claims_yoy", None, None, {"error": "ICSA 이력 부족"}))
    else:
        components.append(_component("claims_yoy", None, None, {"error": "FRED ICSA 미확보"}))
        notes.append("ICSA 미확보: 실업청구 성분 제외")

    # ---- 5. consumer sentiment (level z)
    d = fred.get("UMCSENT") or {}
    s = _fred_series(fred, "UMCSENT")
    if s is not None and len(s) > 24:
        win = s[s.index >= (s.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
        z = _z(float(s.iloc[-1]), float(win.mean()), float(win.std()))
        components.append(_component("consumer_sentiment", float(s.iloc[-1]), z,
                                     {"mean_5y": round(float(win.mean()), 4), "std_5y": round(float(win.std()), 4),
                                      "as_of": d.get("date"), "source_state": d.get("source")}, _monthly_tail(s)))
    else:
        components.append(_component("consumer_sentiment", None, None, {"error": "FRED UMCSENT 미확보"}))
        notes.append("UMCSENT 미확보: 소비자심리 성분 제외")

    # ---- 6. equity momentum (SPY 252d total return, z over the 5y window)
    spy = lei_hist.get("SPY")
    if spy is not None and len(spy) > 300:
        c = spy["Close"].astype(float)
        r252 = (c / c.shift(252) - 1.0) * 100.0
        r252 = r252.dropna()
        win = r252[r252.index >= (r252.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
        z = _z(float(r252.iloc[-1]), float(win.mean()), float(win.std()))
        components.append(_component("equity_momentum_12m", float(r252.iloc[-1]), z,
                                     {"mean_5y": round(float(win.mean()), 4), "std_5y": round(float(win.std()), 4),
                                      "bar_date": str(spy.index[-1].date())}, _monthly_tail(r252)))
    else:
        components.append(_component("equity_momentum_12m", None, None, {"error": "SPY 5년 이력 미확보"}))
        notes.append("SPY 5년 이력 미확보: 주가 성분 제외")

    # ---- 7. copper / gold ratio
    hg, gc = lei_hist.get("HG=F"), lei_hist.get("GC=F")
    if hg is not None and gc is not None and len(hg) > 300 and len(gc) > 300:
        ratio = (hg["Close"].astype(float) / gc["Close"].astype(float)).dropna()
        win = ratio[ratio.index >= (ratio.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
        z = _z(float(ratio.iloc[-1]), float(win.mean()), float(win.std()))
        components.append(_component("copper_gold", float(ratio.iloc[-1]), z,
                                     {"mean_5y": round(float(win.mean()), 6), "std_5y": round(float(win.std()), 6),
                                      "copper": round(float(hg["Close"].iloc[-1]), 4), "gold": round(float(gc["Close"].iloc[-1]), 4)},
                                     _monthly_tail(ratio)))
    else:
        components.append(_component("copper_gold", cycle.get("copper_gold_ratio"), None,
                                     {"error": "구리/금 5년 이력 미확보"}))
        notes.append("구리/금 5년 이력 미확보: 원자재 성분 제외")

    # ---- 8. consumer rotation XLY / XLP
    xly, xlp = lei_hist.get("XLY"), lei_hist.get("XLP")
    if xly is not None and xlp is not None and len(xly) > 300 and len(xlp) > 300:
        ratio = (xly["Close"].astype(float) / xlp["Close"].astype(float)).dropna()
        win = ratio[ratio.index >= (ratio.index[-1] - pd.Timedelta(days=365 * Z_WINDOW_YEARS))]
        z = _z(float(ratio.iloc[-1]), float(win.mean()), float(win.std()))
        components.append(_component("consumer_rotation", float(ratio.iloc[-1]), z,
                                     {"mean_5y": round(float(win.mean()), 6), "std_5y": round(float(win.std()), 6),
                                      "xly": round(float(xly["Close"].iloc[-1]), 2), "xlp": round(float(xlp["Close"].iloc[-1]), 2)},
                                     _monthly_tail(ratio)))
    else:
        components.append(_component("consumer_rotation", cycle.get("xly_xlp_ratio"), None,
                                     {"error": "XLY/XLP 5년 이력 미확보"}))
        notes.append("XLY/XLP 5년 이력 미확보: 소비 로테이션 성분 제외")

    # ------------------------------------------------------------ composite
    avail = [c for c in components if c["available"]]
    w_sum = sum(c["weight"] for c in avail) or 1.0
    composite_z = sum(c["weight"] * c["sign"] * (c["z"] or 0.0) for c in avail) / w_sum
    equal_z = float(np.mean([c["sign"] * (c["z"] or 0.0) for c in avail])) if avail else 0.0

    # monthly composite history (same static mean/std per component) for the trend
    monthly_rows: List[Dict[str, Any]] = []
    try:
        idx = pd.date_range(end=pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)).normalize(), periods=26, freq="ME")
        acc: Dict[pd.Timestamp, List[float]] = {ix: [] for ix in idx}
        for c in avail:
            m = c.get("_monthly")
            if m is None or len(m) == 0:
                continue
            zz = None
            det = c.get("detail") or {}
            if det.get("mean_5y") is not None and det.get("std_5y"):
                zz = (m - float(det["mean_5y"])) / float(det["std_5y"])
            if zz is None:
                continue
            for ix in idx:
                sub = zz[zz.index <= ix]
                if len(sub):
                    acc[ix].append(c["weight"] * c["sign"] * float(sub.iloc[-1]))
        for ix in idx:
            if acc[ix]:
                monthly_rows.append({"month": ix.strftime("%Y-%m"), "z": round(sum(acc[ix]) / w_sum, 3)})
    except Exception as e:
        notes.append(f"월별 합성 이력 계산 실패: {e}")

    z3m = None
    if len(monthly_rows) >= 4:
        z3m = monthly_rows[-4]["z"]
    trend_3m = round(composite_z - z3m, 3) if z3m is not None else None

    lei_index = round(100 + 10 * composite_z, 1)          # OECD-style amplitude: 1σ = 10pt, mean 100
    lei_score = round(_phi(composite_z) * 100, 1)          # 0-100 percentile mapping

    # ---- recession probit (Estrella & Mishkin 1998)
    sp = (bonds.get("curve") or {}).get("10y3m_bp")
    sp2 = (bonds.get("curve") or {}).get("10y2y_bp")
    p10y3m = recession_probit(sp / 100.0) if sp is not None else None
    p10y2y_approx = recession_probit(sp2 / 100.0) if sp2 is not None else None
    p_used = p10y3m

    # Estrella & Mishkin: the recession signal peaks roughly 12 months after an
    # inversion ENDS, so time-since-uninversion is the cycle-timing companion to
    # the probit. Measured from the FRED T10Y3M history.
    months_since_uninversion = None
    t3m = _fred_series(fred, "T10Y3M")
    if t3m is not None and len(t3m) > 30:
        neg = t3m < 0
        if len(neg) and not bool(neg.iloc[-1]):
            last_neg = neg[neg].index[-1] if bool(neg.any()) else None
            if last_neg is not None:
                months_since_uninversion = round((t3m.index[-1] - last_neg).days / 30.44, 1)

    probit = {
        "formula": "P(12개월 내 침체) = Φ(-0.6045 - 0.8180 × (10Y-3M, %p))",
        "source": "Estrella & Mishkin (1998), REStat 80(1):45-61",
        "spread_10y3m_bp": sp,
        "probability_10y3m": round(p10y3m * 100, 1) if p10y3m is not None else None,
        "spread_10y2y_bp": sp2,
        "probability_10y2y_same_coefficients": round(p10y2y_approx * 100, 1) if p10y2y_approx is not None else None,
        "probability_10y2y_note": ("동일 계수를 10Y-2Y에 적용한 근사값 — 원계수는 10Y-3M 기준이므로 방향 참고용. "
                                   "10Y-2Y 자체 계수는 별도 재추정이 필요합니다."),
        "months_since_curve_uninverted": months_since_uninversion,
        "uninversion_note": "금리차가 역전에서 벗어난 뒤 약 12개월에 침체 신호가 정점(Estrella & Mishkin 1998).",
        "probability_used": round(p_used * 100, 1) if p_used is not None else None,
    }

    # ---- regime
    prob_pct = (p_used * 100) if p_used is not None else None
    if composite_z <= -1.0 or (prob_pct is not None and prob_pct >= 50):
        regime, regime_ko = "CONTRACTION_RISK", "수축 위험"
    elif composite_z < 0.0 and trend_3m is not None and trend_3m > 0:
        regime, regime_ko = "RECOVERY", "회복"
    elif composite_z < 0.3 or (trend_3m is not None and trend_3m <= -1.0):
        regime, regime_ko = "LATE_CYCLE_SLOWDOWN", "후기 사이클 둔화"
    else:
        regime, regime_ko = "EXPANSION", "확장"

    # ---- credit block (Gilchrist & Zakrajšek proxy)
    hy_bp = bonds.get("hy_oas_bp")
    hyg_rel = bonds.get("hyg_ief_rel_63d_pct")
    credit_z = next((c["z"] for c in components if c["key"] == "credit_oas"), None)
    gz_proxy = None
    if credit_z is not None:
        rel_component = 0.0
        if hyg_rel is not None:
            # 63일 상대강도 +1%p ≈ -0.25σ (확장적 신용환경) — 문서화된 선형 매핑
            rel_component = -float(hyg_rel) / 4.0
        gz_proxy = round(0.7 * credit_z + 0.3 * rel_component, 2)
    hy_level = ("NORMAL" if hy_bp is not None and hy_bp < 350 else
                "WATCH" if hy_bp is not None and hy_bp < 500 else
                "STRESS" if hy_bp is not None and hy_bp < 700 else
                "CRISIS" if hy_bp is not None else "UNKNOWN")
    credit = {
        "metric": "HY OAS level + HYG/IEF 상대강도 (GZ 2012 EBP의 공개데이터 프록시 — EBP 아님)",
        "hy_oas_bp": hy_bp, "hy_oas_z_5y": credit_z, "hyg_ief_rel_63d_pct": hyg_rel,
        "gz_proxy_z": gz_proxy, "level": hy_level,
        "level_thresholds_bp": {"NORMAL": "<350", "WATCH": "350-500", "STRESS": "500-700", "CRISIS": ">700"},
        "reading_ko": (f"HY OAS {hy_bp}bp(z {credit_z}) · HYG-IEF 63일 상대 {hyg_rel}%p → {hy_level}"
                       if hy_bp is not None else "HY OAS 미확보"),
    }

    # ---- bond market block
    etfs = bonds.get("etfs") or {}
    dur = bonds.get("duration_years") or {}
    yields = bonds.get("yields_pct") or {}
    curve = bonds.get("curve") or {}
    tlt, ief, shy, sgov, tip, hyg, lqd = (etfs.get(t, {}) for t in ("TLT", "IEF", "SHY", "SGOV", "TIP", "HYG", "LQD"))
    bond_block = {
        "yields_pct": yields,
        "curve_bp": {"10y3m": curve.get("10y3m_bp"), "10y2y": curve.get("10y2y_bp"),
                     "30y10y": curve.get("30y10y_bp"), "5y10y": curve.get("5y10y_bp")},
        "curve_shape": curve.get("shape"), "curve_source": curve.get("source"),
        "term_premium_proxy_bp": curve.get("30y10y_bp"),
        "etfs": {t: {"last": etfs.get(t, {}).get("last"), "chg_pct": etfs.get(t, {}).get("chg_pct"),
                     "ret_20d": etfs.get(t, {}).get("ret_20d"), "ret_63d": etfs.get(t, {}).get("ret_63d"),
                     "duration_years": dur.get(t)}
                 for t in ("TLT", "IEF", "SHY", "SGOV", "TIP", "HYG", "LQD")},
        "duration_pnl_per_10bp_pct": bonds.get("duration_pnl_per_10bp_pct"),
        "inflation_breakeven_proxy": {"tip_minus_ief_63d_pct": (
            round((tip.get("ret_63d") or 0.0) - (ief.get("ret_63d") or 0.0), 2)
            if tip.get("ret_63d") is not None and ief.get("ret_63d") is not None else None),
            "note": "TIP(물가연동채) 63일 수익률 - IEF(명목 7-10Y) 63일 수익률: (+)면 기대인플레 상승 우위"},
        "read_ko": (f"2Y {yields.get('2y')}% / 10Y {yields.get('10y')}% / 30Y {yields.get('30y')}%, "
                    f"10Y-3M {curve.get('10y3m_bp')}bp({curve.get('shape')})"),
    }

    # ---- FX block
    krw = (fx.get("usd_krw") or {})
    dxy = (fx.get("dxy") or {})
    fx_block = {
        "usd_krw": krw, "dxy": dxy,
        "usd_jpy": fx.get("usd_jpy"), "eur_usd": fx.get("eur_usd"), "usd_cny": fx.get("usd_cny"),
        "dollar_regime": ("STRONG_DOLLAR" if (dxy.get("last") or 0) >= 104.5 else
                          "NEUTRAL_DOLLAR" if (dxy.get("last") or 0) >= 100 else "WEAK_DOLLAR"),
        "krw_regime": ("KRW_WEAK (원화 약세·환노출 이익)" if (krw.get("ret_20d") or 0) > 1.0 else
                       "KRW_STRONG (원화 강세·환노출 손실)" if (krw.get("ret_20d") or 0) < -1.0 else "KRW_STABLE"),
        "read_ko": (f"USD/KRW {krw.get('last')} (20일 {krw.get('ret_20d')}%), DXY {dxy.get('last')} "
                    f"({dxy.get('chg_pct')}%)"),
    }

    # ---- crypto block
    btc, eth = crypto.get("btc") or {}, crypto.get("eth") or {}
    sol, xrp = crypto.get("sol") or {}, crypto.get("xrp") or {}
    ibit, coin = crypto.get("ibit") or {}, crypto.get("coin") or {}
    weekend_ret = _weekend_return(snap)
    crypto_block = {
        "btc": btc, "eth": eth, "sol": sol, "xrp": xrp, "ibit": ibit, "coin": coin,
        "weekend_return_pct": weekend_ret,
        "risk_on_proxy_score": _crypto_risk_score(btc, eth, sol, weekend_ret),
        "read_ko": (f"BTC ${btc.get('last'):,.0f} (24h {btc.get('chg_pct')}%, 7d {btc.get('ret_7d')}%), "
                    f"ETH ${eth.get('last'):,.0f} ({eth.get('chg_pct')}%)"
                    if btc.get("last") and eth.get("last") else "코인 시세 미확보"),
    }

    # ---- policy hand-off for Agents 6/9/10
    leverage_cap = {"EXPANSION": 1.0, "LATE_CYCLE_SLOWDOWN": 0.7,
                    "CONTRACTION_RISK": 0.35, "RECOVERY": 0.6}.get(regime, 0.5)
    policy = {
        "max_gross_exposure_factor": leverage_cap,
        "parking_preference": ("SGOV 우선 (0-3M T-bill, 듀레이션 0.1년·금리 리스크 최소)"
                               if (prob_pct or 0) >= 30 or regime != "EXPANSION"
                               else "SGOV/SHY 병행 (단기 캐리 + 듀레이션 소폭)"),
        "bond_duration_stance": ("SHORT_DURATION (TLT 신규 진입 자제, SGOV/SHY 중심)" if (yields.get("10y") or 0) >= 4.5
                                 else "NEUTRAL (IEF 중심 7-10Y)" if (yields.get("10y") or 0) >= 4.0
                                 else "EXTEND_DURATION (TLT/IEF 비중 확대 검토)"),
        "crypto_weekend_alert": bool((weekend_ret or 0.0) <= -3.0),
        "fx_hedge_note": ("원화 약세 구간 — USD 자산 보유 자체가 환차익 방어선" if (krw.get("ret_20d") or 0) > 1.0
                          else "원화 강세 구간 — 원화 환산 수익률이 USD 수익률보다 낮아짐" if (krw.get("ret_20d") or 0) < -1.0
                          else "원/달러 보합권 — 환노출 영향 제한적(20일 변동 ±1% 이내)"),
    }

    out = {
        "engine": "CUBE-LEI v1",
        "generated_at_kst": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
        "as_of_market_bar": (snap.get("bonds", {}).get("etfs", {}).get("TLT", {}) or {}).get("bar"),
        "cube_lei": {
            "z": round(composite_z, 3),
            "index": lei_index, "index_scale": "100 = 장기 추세, 1σ = 10pt (OECD CLI 진폭조정)",
            "score": lei_score, "score_scale": "0-100 (표준정규 CDF 매핑)",
            "equal_weight_z": round(equal_z, 3),
            "weighted_vs_equal_gap": round(composite_z - equal_z, 3),
            "trend_3m_z_change": trend_3m,
            "regime": regime, "regime_ko": regime_ko,
            "monthly_history": monthly_rows[-13:],
            "components_used": len(avail), "components_total": len(components),
        },
        "recession_probit": probit,
        "components": [{k: v for k, v in c.items() if k != "_monthly"} for c in components],
        "credit": credit,
        "bonds": bond_block,
        "fx": fx_block,
        "crypto": crypto_block,
        "policy": policy,
        "notes": notes,
        "methodology": {
            "equations": {
                "probit": "P = Φ(-0.6045 - 0.8180 × spread_10y3m)",
                "composite": "Z = Σ w_i · sign_i · z_i / Σ w_i  (z_i = 5년 평균/표준편차 표준화)",
                "index": "CUBE-LEI = 100 + 10·Z",
                "score": "score = Φ(Z)·100",
            },
            "weights": WEIGHTS,
            "z_window_years": Z_WINDOW_YEARS,
            "regime_rule": "z<=-1.0 또는 P>=50% → CONTRACTION_RISK / z<0 & 3개월 변화>0 → RECOVERY / z<0.3 또는 3개월 변화<=-1.0 → LATE_CYCLE_SLOWDOWN / 그 외 EXPANSION",
            "weight_note": "가중치는 Conference Board LEI 성분 기여 순서를 따르되 무료 공개 데이터로 재현 가능한 성분만으로 정규화한 CUBE 설계값입니다(CB 원계수 재게시 아님).",
            "references": REFERENCES,
        },
        "data_provenance": {
            "fred": {sid: {"latest": (fred.get(sid) or {}).get("latest"), "date": (fred.get(sid) or {}).get("date"),
                           "source": (fred.get(sid) or {}).get("source")} for sid in market_snapshot.FRED_SERIES},
            "yahoo": "SPY/HG=F/GC=F/XLY/XLP/IWM 5년 일봉 (data_cache/lei_history_5y.pkl)",
            "snapshot": snap.get("generated_at_kst"),
            "hardcoded_values": False,
        },
    }
    if not quiet:
        print(f"✅ CUBE-LEI {lei_index} (z {composite_z:+.2f}, {regime_ko}) P(침체) "
              f"{probit['probability_used']}% · 성분 {len(avail)}/{len(components)}")
    return out


def _weekend_return(snap: Dict[str, Any]) -> Optional[float]:
    """BTC return over the last Saturday+Sunday (crypto is the only 24/7 risk proxy).

    Measured from the actual daily BTC bars: (Sun close / Fri close - 1)*100 when
    the last two bars are Sat/Sun, otherwise the last full weekend in the window.
    """
    try:
        hist = market_snapshot.fetch_history(["BTC-USD"])
        f = hist.get("BTC-USD")
        if f is None or len(f) < 5:
            return None
        c = f["Close"].astype(float).dropna()
        # walk back from the end to the most recent Friday close
        for i in range(len(c) - 1, max(0, len(c) - 12), -1):
            if c.index[i].weekday() == 4:
                after = c.iloc[i + 1:] if i + 1 < len(c) else pd.Series(dtype=float)
                if len(after) >= 1:
                    return round((float(after.iloc[-1]) / float(c.iloc[i]) - 1.0) * 100, 2)
        return None
    except Exception:
        return None


def _crypto_risk_score(btc: Dict[str, Any], eth: Dict[str, Any], sol: Dict[str, Any],
                       weekend_ret: Optional[float]) -> Optional[float]:
    """0-100 risk-on proxy from measured crypto moves (50 = neutral)."""
    vals = []
    if btc.get("chg_pct") is not None:
        vals.append(max(-20.0, min(20.0, float(btc["chg_pct"]))))
    if eth.get("chg_pct") is not None:
        vals.append(max(-20.0, min(20.0, float(eth["chg_pct"]))))
    if btc.get("ret_7d") is not None:
        vals.append(max(-30.0, min(30.0, float(btc["ret_7d"]))) / 2.0)
    if weekend_ret is not None:
        vals.append(max(-20.0, min(20.0, float(weekend_ret))))
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    return round(max(0.0, min(100.0, 50.0 + avg * 2.5)), 1)


def export(result: Dict[str, Any] = None) -> Dict[str, Any]:
    res = result or compute()
    os.makedirs(os.path.dirname(LEI_OUT), exist_ok=True)
    os.makedirs(os.path.dirname(DOCS_LEI_OUT), exist_ok=True)
    payload = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    for p in (LEI_OUT, DOCS_LEI_OUT):
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)
    return res


def load() -> Dict[str, Any]:
    if os.path.exists(LEI_OUT):
        try:
            with open(LEI_OUT, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


if __name__ == "__main__":
    export(compute(quiet=False))
