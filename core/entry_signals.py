"""
Entry signals — 무인 집행이 실제로 '살 수 있는' 신호만 계산한다.

여기 있는 신호는 전부 사용자가 이미 승인한 것들이다.

1. `dip_daily`      — DIP_SWING(11-14 21:29 승인): RSI2 ≤ 20 · %B ≤ 0.15 · RVOL ≥ 1.0 이면
   다음 세션 시가 진입, ATR×1.5 대칭 브래킷, 최대 3세션. 1년 +19.19%/PF 1.98, 2년 +38.17%
   (60분봉·왕복 0.24% 마찰). **RVOL ≥ 1.0은 필수**(2026-11-18 14:25 원복 — 가점 전환은 60일
   기대값 -$10.9로 기대값이 떨어져 적용하지 않음. 11-18 11:37~14:15 사이에만 가점이었다).
2. `dip_intraday`   — 신중 모드(11-16 23:43 승인)의 장중 규칙: 00:00 KST 이후
   DIP_SWING 등급 신호가 나올 때만 현금 15~20%로 1회 진입. 5분봉 VWAP 돌파 단독
   진입은 60일 실측 건당 -0.29%(SOXL)/-0.30%(TQQQ)로 폐기됐다.
3. `bear_alpha`     — 하락장 숏(11-13 수립·11-14 게이트 상향·**2026-11-18 1-of-1 완화**):
   VIX ≥ 24.0(하한 고정) + 확인 1개(DXY ≥ 104.5 / 평균 센티먼트 < 40 / 가격구조
   SOXL < 세션 VWAP). 2-of-2는 11-11~17 숏 15건 전부 0/15로 발동하지 못했다.
4. `nvda_gap_soxs`  — 11-18 NVDA 실적 반응일(11-17 12:00 사용자 승인): 갭 ≥ +2%면
   SOXS 1주 시가 매수·05:55 종가 청산. 19회 건당 +2.77%(승률 47.4%, t=1.37).
5. `c5_open30`      — C5 파일럿(2026-11-18 11:37 승인): 개장 30분(10:00 ET) 가격이 전일
   종가 대비 위/아래인지로 방향을 정해 1일 1회 진입, 15:40 ET 청산, 손절 3×ATR,
   예산 $150. 60일 118건 +$40.09(PF 1.20)·5일 +$0.10 — 통계적 유의성 미확보(t=0.71)라
   실전은 소액 파일럿으로만 돌린다. 실적 반응일은 진입 제외(블랙아웃).

신호는 '점수'가 아니라 조건 충족 여부로만 돌려준다. 조건이 애매하면 진입하지 않는다.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
INTRADAY_CACHE = os.path.join(DATA, "intraday_5m_cache.json")

# RVOL ≥ 1.0은 필수 조건이다(2026-11-18 14:25 원복). RSI2·%B와 함께 세 조건 모두 통과해야 신호.
DIP_THRESHOLDS = {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0,
                  "rvol_required": True, "rvol_bonus_max": 20.0}

# 숏 게이트 — VIX 하한 24.0은 고정(SAFE: config로도 내릴 수 없다), 확인은 1-of-1.
BEAR_GATE = {"vix_min": 24.0, "vix_floor": 24.0, "confirmations_required": 1,
             "confirmations": {"dxy_min": 104.5, "sentiment_max": 40.0,
                               "price_structure": True}}
GAP_SOXS = {"reference_close_usd": 156.14, "gap_min_pct": 2.0, "ticker": "SOXS",
            "disaster_stop_pct": -8.0, "exit_kst": "05:55"}

# C5 파일럿 — 개장 30분 방향 1일 1회 (config/swing_approval.json가 정본)
C5_OPEN30 = {
    "enabled": True,
    "entry_min_after_open": 30,        # 10:00 ET = 00:00 KST
    "entry_window_min": [30, 60],      # 00:00~00:30 KST 사이 1회(사이클 1회 누락 허용)
    "exit_kst": "05:40",               # 15:40 ET
    "sl_atr_mult": 3.0,
    "budget_usd": 150.0,
    "min_gap_pct": 0.0,                # 10:00 가격이 전일 종가와 같은 경우는 SHORT
    "long_tickers": ["SOXL", "TQQQ"],
    "short_tickers": ["SOXS", "SQQQ"],
    "blackout_dates": ["2026-11-18"],  # NVDA 실적 반응일 — SOXS 조건부 집행과 겹침 방지
    "earnings_reaction_blackout": True,
}
EARNINGS_CALENDAR = os.path.join(DATA, "earnings_calendar.json")


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def _load(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# --------------------------------------------------------------- intraday bars
def fetch_intraday_bars(symbols: List[str], cache_min: float = 3.0,
                        force: bool = False) -> Dict[str, List[Dict[str, float]]]:
    """5분봉(최근 2일) 종가·고가·저가·거래량. 캐시 TTL 기본 3분."""
    cache = _load(INTRADAY_CACHE)
    if cache.get("bars") and not force and cache.get("fetched_ts_utc"):
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cache["fetched_ts_utc"])).total_seconds() / 60
            if age <= cache_min and set(symbols).issubset(set((cache.get("bars") or {}).keys())):
                return cache["bars"]
        except Exception:
            pass
    bars: Dict[str, List[Dict[str, float]]] = {}
    try:
        import yfinance as yf
        for sym in symbols:
            try:
                df = yf.download(sym, period="2d", interval="5m", progress=False, auto_adjust=False)
                if df is None or len(df) == 0:
                    continue
                if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                    df.columns = [c[0] for c in df.columns]
                rows = []
                for idx, row in df.tail(120).iterrows():
                    try:
                        c = float(row["Close"])
                        if c != c:
                            continue
                        rows.append({"t": str(idx), "c": c,
                                     "h": float(row["High"]), "l": float(row["Low"]),
                                     "v": float(row.get("Volume") or 0)})
                    except Exception:
                        continue
                if len(rows) >= 12:
                    bars[sym] = rows
            except Exception:
                continue
    except Exception:
        return bars
    if bars:
        cache.update({"bars": bars, "fetched_ts_utc": datetime.now(timezone.utc).isoformat(),
                      "fetched_at_kst": _now_kst().strftime("%Y-%m-%d %H:%M:%S")})
        try:
            with open(INTRADAY_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return bars


def _rsi_last(closes: List[float], n: int = 2) -> Optional[float]:
    import numpy as np
    import pandas as pd
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50)
    return float(rsi.iloc[-1])


def dip_signal_from_metrics(m: Optional[Dict[str, Any]],
                            thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """5분봉 지표 → DIP 판정(필수 3조건 + 거래량 가점).

    필수: RSI2 ≤ 20 · %B ≤ 0.15 · RVOL ≥ `rvol_min`(1.0). 과매도 극단 + 거래량 확인이
    같이 붙은 자리에서만 산다(2026-11-18 14:25 원복). RVOL이 1.0을 넘으면 score에
    최대 `rvol_bonus_max`점을 더해 같은 세션 진입 한도 안에서 우선순위를 정한다.
    `rvol_required=False`는 호출 측이 명시할 때만 쓰는 연구·비교용 경로다.
    """
    th = dict(DIP_THRESHOLDS)
    for k, v in (thresholds or {}).items():
        if k in th:
            th[k] = v
    if not m:
        return {"ok": False, "why_not": "5분봉 부족", "score": 0.0, "bonus": {}}
    rsi2, pct_b, rvol = m.get("rsi2"), m.get("pct_b"), m.get("rvol")
    rsi2_max = float(th["rsi2_max"])
    pct_b_max = float(th["pct_b_max"])
    rvol_min = float(th["rvol_min"])
    rvol_required = bool(th.get("rvol_required", False))
    misses = []
    if rsi2 is None or float(rsi2) > rsi2_max:
        misses.append(f"RSI2 {rsi2}>{rsi2_max:.0f}")
    if pct_b is None or float(pct_b) > pct_b_max:
        misses.append(f"%B {pct_b}>{pct_b_max:.2f}")
    if rvol_required and (rvol is None or float(rvol) < rvol_min):
        misses.append(f"RVOL {rvol}<{rvol_min}(필수)")
    bonus = 0.0
    if rvol is not None and float(rvol) >= rvol_min:
        bonus = min(float(th.get("rvol_bonus_max", 20.0)), (float(rvol) - rvol_min) * 20.0 + 5.0)
    depth = 0.0
    if rsi2 is not None:
        depth += min(30.0, max(0.0, (rsi2_max - float(rsi2))) * 3.0)
    if pct_b is not None:
        depth += min(30.0, max(0.0, (pct_b_max - float(pct_b))) * 150.0)
    score = round(depth + bonus, 1)
    return {"ok": not misses, "why_not": " 또는 ".join(misses) if misses else None,
            "score": score, "boundary": round(depth, 1), "bonus": {"rvol": round(bonus, 1)},
            "rvol_required": rvol_required}


def resolve_entry_filters(strategy_filters: Optional[Dict[str, Any]] = None,
                          approval_conditions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """전략 파라미터 + 승인 조건(`config/swing_approval.json entry_conditions`) 합성.

    승인 조건은 하한(floor)이다 — 전략 파라미터나 방어 모드가 승인 조건보다 **느슨하게**
    풀 수는 없고 더 엄격하게만 조일 수 있다. 그래서 `rvol_required`는 둘 중 하나라도
    True면 True이고, RVOL도 더 높은 쪽을 쓴다(2026-11-18 14:25 원복이
    `data/strategy_params.json` 상태와 무관하게 집행된다).
    """
    out = dict(DIP_THRESHOLDS)
    for k, v in (strategy_filters or {}).items():
        if k in out and v is not None:
            out[k] = v
    ap = approval_conditions or {}
    for key in ("rsi2_max", "pct_b_max", "rvol_min"):
        if ap.get(key) is None:
            continue
        try:
            a, b = float(out[key]), float(ap[key])
        except (TypeError, ValueError):
            continue
        out[key] = max(a, b) if key == "rvol_min" else min(a, b)
    out["rvol_required"] = bool(out.get("rvol_required")) or bool(ap.get("rvol_required"))
    out["rvol_bonus_max"] = float(out.get("rvol_bonus_max") or 20.0)
    return out


def intraday_atr_pct(bars: List[Dict[str, float]]) -> Optional[float]:
    """5분봉 ATR% — 장중 손절·트레일 폭의 기준(일봉 ATR 20%대와 섞지 않는다)."""
    if not bars or len(bars) < 8:
        return None
    try:
        import pandas as pd
        df = pd.DataFrame(bars)
        c = df["c"].astype(float)
        tr = pd.concat([df["h"] - df["l"], (df["h"] - c.shift()).abs(), (df["l"] - c.shift()).abs()],
                       axis=1).max(axis=1)
        atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
        px = float(c.iloc[-1])
        return round(atr / px * 100, 2) if px > 0 else None
    except Exception:
        return None


def intraday_dip_metrics(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """5분봉 기준 RSI2 · 볼린저 %B · RVOL · ATR% (DIP 등급 판정용)."""
    import numpy as np
    import pandas as pd
    if not bars or len(bars) < 21:
        return None
    df = pd.DataFrame(bars)
    c = df["c"].astype(float)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    lo, hi = mid - 2 * sd, mid + 2 * sd
    pct_b = float(((c - lo) / (hi - lo).replace(0, np.nan)).iloc[-1])
    tr = pd.concat([df["h"] - df["l"], (df["h"] - c.shift()).abs(), (df["l"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    vol_avg = float(df["v"].rolling(20).mean().shift(1).iloc[-1] or 0)
    rvol = float(df["v"].iloc[-1] / vol_avg) if vol_avg > 0 else None
    return {"rsi2": round(_rsi_last(list(c), 2), 1), "pct_b": round(pct_b, 3),
            "rvol": round(rvol, 2) if rvol is not None else None,
            "atr_pct": round(atr / float(c.iloc[-1]) * 100, 2),
            "price": round(float(c.iloc[-1]), 2), "bars": int(len(c))}


def dip_intraday(symbols: List[str], cache_min: float = 3.0,
                 thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    th = dict(DIP_THRESHOLDS)
    for k, v in (thresholds or {}).items():
        if k in th:
            th[k] = v
    bars = fetch_intraday_bars(symbols, cache_min=cache_min)
    out = {"signals": [], "watch": [], "thresholds": th}
    for sym in symbols:
        m = intraday_dip_metrics(bars.get(sym) or [])
        if not m:
            out["watch"].append({"symbol": sym, "why_not": "5분봉 부족"})
            continue
        m["symbol"] = sym
        verdict = dip_signal_from_metrics(m, th)
        m.update({"score": verdict["score"], "bonus": verdict["bonus"],
                  "rvol_bonus": (verdict["bonus"] or {}).get("rvol", 0.0)})
        if verdict["ok"]:
            m["signal"] = "DIP_SWING_INTRADAY"
            out["signals"].append(m)
        else:
            m["why_not"] = verdict["why_not"]
            out["watch"].append(m)
    # 가점(score) 순 — 같은 세션 진입 한도(3건) 안에서 더 깊은 과매도 + 거래량 실린 신호가 먼저 간다
    out["signals"].sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return out


# --------------------------------------------------------------- dynamic universe
def entry_universe(core: Optional[List[str]] = None, max_age_h: float = 6.0) -> List[str]:
    """진입 신호 탐색 대상 — 코어 4종목 + 동적 스크리너 선정 종목(2026-11-26).

    스크리너(`core/universe_screener`) 결과가 없거나 오래되면 코어만 돌려준다.
    무인 집행이 이 함수를 쓰면 ETF·나스닥100 대형주까지 같은 DIP 조건으로 스캔된다.
    """
    try:
        from core import universe_screener
        base = list(core or universe_screener.CORE_UNIVERSE)
        return universe_screener.effective_universe(core=base, max_age_h=max_age_h)
    except Exception:
        return list(core or [])


# --------------------------------------------------------------- daily signals
def dip_daily(daily: Dict[str, Any], universe: Optional[List[str]] = None,
              thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """완료된 세션 일봉 기준 DIP_SWING 신호(다음 세션 시가 진입용).

    `universe`를 비우면 코어 + 동적 스크리닝 후보 전체를 스캔한다.
    """
    try:
        from core import swing_engine
        params = dict(swing_engine.DIP_PARAMS)
        if universe is None:
            universe = entry_universe()
        if universe:
            params["universe"] = list(universe)
        for k, v in (thresholds or {}).items():
            if k in ("rsi2_max", "pct_b_max", "rvol_min"):
                params[k] = float(v)
            elif k == "rvol_required":
                params[k] = bool(v)
        scan = swing_engine.scan_latest(daily, params)
        return {"signals": scan.get("signals", []), "watch": scan.get("watch", []),
                "as_of": scan.get("as_of"), "params": params}
    except Exception as e:
        return {"signals": [], "watch": [], "error": str(e)}


# --------------------------------------------------------------- bear alpha
def intraday_vwap_state(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """5분봉 → 당일 세션 VWAP 대비 현재가 위치(숏 게이트의 가격구조 확인)."""
    if not bars:
        return None
    last_day = str(bars[-1].get("t"))[:10]
    today = [b for b in bars if str(b.get("t"))[:10] == last_day]
    if len(today) < 6:
        return None
    num = sum(((float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0) * float(b.get("v") or 0)
              for b in today)
    den = sum(float(b.get("v") or 0) for b in today)
    if den <= 0:
        px = float(today[-1]["c"])
        vwap = sum((float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0 for b in today) / len(today)
    else:
        vwap = num / den
        px = float(today[-1]["c"])
    return {"price": round(px, 2), "vwap": round(vwap, 2), "below_vwap": px < vwap,
            "bars": len(today), "session_ts": last_day}


def bear_alpha_gate(snapshot: Dict[str, Any], sentiment_avg: Optional[float] = None,
                    price_structure: Optional[Dict[str, Any]] = None,
                    confirmations_required: Optional[int] = None) -> Dict[str, Any]:
    """VIX ≥ 24.0(하한 고정) + 확인 1개.

    확인 후보: DXY ≥ 104.5 / 평균 센티먼트 < 40 / 가격구조(SOXL < 당일 세션 VWAP).
    2-of-2였던 11-11~17 구간에서 숏 15건이 전부 0/15로 발동하지 못했다(VIX<24 12건,
    VIX≥24여도 DXY 103.4<104.5로 확인 0/2 3건). 2026-11-18 사용자 승인으로 1-of-1.

    VIX 하한은 `vix_floor`(24.0) 아래로 내려가지 않는다 — 게이트 완화는 '확인 개수'이지
    '변동성 문턱'이 아니다.
    """
    macro = snapshot.get("macro") or {}
    vix = ((macro.get("vix") or {}).get("last"))
    dxy = ((macro.get("dxy") or {}).get("last"))
    required = int(confirmations_required or BEAR_GATE["confirmations_required"])
    required = max(1, min(3, required))
    confirmations = []
    if dxy is not None and float(dxy) >= BEAR_GATE["confirmations"]["dxy_min"]:
        confirmations.append(f"DXY {float(dxy):.2f} ≥ {BEAR_GATE['confirmations']['dxy_min']}")
    if sentiment_avg is not None and float(sentiment_avg) < BEAR_GATE["confirmations"]["sentiment_max"]:
        confirmations.append(f"센티먼트 {float(sentiment_avg):.1f} < {BEAR_GATE['confirmations']['sentiment_max']}")
    if price_structure and price_structure.get("below_vwap"):
        confirmations.append(f"SOXL ${price_structure.get('price')} < 세션 VWAP ${price_structure.get('vwap')}")
    vix_ok = vix is not None and float(vix) >= max(BEAR_GATE["vix_min"], BEAR_GATE["vix_floor"])
    armed = bool(vix_ok and len(confirmations) >= required)
    return {"vix": vix, "dxy": dxy, "confirmations": confirmations,
            "confirmations_required": required, "price_structure": price_structure,
            "armed": armed,
            "gate": f"VIX ≥ {max(BEAR_GATE['vix_min'], BEAR_GATE['vix_floor'])} + 확인 {required}개",
            "reason": (None if armed else
                       (f"VIX {vix} < {BEAR_GATE['vix_floor']}" if not vix_ok else
                        f"확인 {len(confirmations)}/{required} — DXY·센티먼트·VWAP 모두 미충족"))}


# --------------------------------------------------------------- NVDA gap → SOXS
def nvda_gap_soxs(nvda_prev_close: Optional[float], nvda_open: Optional[float],
                  reference_close: float = GAP_SOXS["reference_close_usd"]) -> Dict[str, Any]:
    """11-18 실적 반응일 규칙: NVDA 갭 ≥ +2% → SOXS 1주 시가 매수, 05:55 청산."""
    ref = float(reference_close or GAP_SOXS["reference_close_usd"])
    if not nvda_open or not nvda_prev_close:
        return {"armed": False, "reason": "개장가/직전 종가 미확인", "reference_close_usd": ref}
    gap = (float(nvda_open) / float(nvda_prev_close) - 1.0) * 100.0
    armed = gap >= GAP_SOXS["gap_min_pct"]
    return {"armed": armed, "gap_pct": round(gap, 2), "threshold_pct": GAP_SOXS["gap_min_pct"],
            "reference_close_usd": ref, "ticker": GAP_SOXS["ticker"],
            "exit_kst": GAP_SOXS["exit_kst"], "disaster_stop_pct": GAP_SOXS["disaster_stop_pct"],
            "reason": (f"NVDA 갭 {gap:+.2f}% ≥ +{GAP_SOXS['gap_min_pct']}% → SOXS 1주" if armed
                       else f"NVDA 갭 {gap:+.2f}% < +{GAP_SOXS['gap_min_pct']}% → 미진입")}


def news_sentiment_avg() -> Optional[float]:
    rep = _load(os.path.join(DATA, "news_sentiment_report.json"))
    v = rep.get("average_sentiment_score")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------- C5 개장30분 방향 파일럿
def c5_config(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = dict(C5_OPEN30)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return cfg


def earnings_reaction_dates(calendar: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """실적 발표 → '반응 세션'(ET 날짜) 목록.

    장 마감 후 발표(예: NVDA 2026-11-17 16:00 ET)는 다음 거래일이 반응일이고,
    개장 전 발표는 같은 날이 반응일이다. 11-12처럼 갭이 장중에 뒤집히는 날
    (NVDA -14% 갭 → 장중 +11.2%) C5 방향 추종이 -$12.76를 잃었으므로 진입을 막는다.
    """
    from core import market_clock
    cal = calendar if calendar is not None else _load(EARNINGS_CALENDAR)
    out: Dict[str, str] = {}
    for sym, e in ((cal or {}).get("entries") or {}).items():
        if not isinstance(e, dict):
            continue
        reaction = None
        report = str(e.get("report_at_kst") or "")
        try:
            t = datetime.strptime(report[:16], "%Y-%m-%d %H:%M")
            et = market_clock.kst_to_et(t)          # 서머타임 반영(KST→ET)
            reaction = (market_clock.next_trading_day(et.date()) if et.time() >= datetime.strptime("16:00", "%H:%M").time()
                        else et.date())
        except Exception:
            reaction = None
        if reaction is None and e.get("earnings_date_et"):
            try:
                reaction = market_clock.next_trading_day(date.fromisoformat(str(e["earnings_date_et"])))
            except Exception:
                reaction = None
        if reaction is not None:
            out[reaction.isoformat()] = sym
    return out


def c5_blackout(session_date: Optional[str], config: Optional[Dict[str, Any]] = None,
                calendar: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """C5 진입 제외일 판정 — ① 명시 블랙아웃 ② 실적 반응일.

    오늘(ET 2026-11-18)은 NVDA 실적 반응일이며 같은 세션에 별도 승인된
    `NVDA_EARNINGS_GAP_SOXS`(SOXS 1주 조건부)가 있으므로, C5가 같은 방향으로 겹쳐
    진입하지 않게 날짜 자체를 제외한다.
    """
    cfg = c5_config(config)
    d = str(session_date or "")[:10]
    explicit = d in {str(x)[:10] for x in (cfg.get("blackout_dates") or [])}
    reaction_sym = None
    if cfg.get("earnings_reaction_blackout", True):
        reaction_sym = earnings_reaction_dates(calendar).get(d)
    blackout = bool(explicit or reaction_sym)
    reasons = []
    if explicit:
        reasons.append(f"명시 블랙아웃 {d}")
    if reaction_sym:
        reasons.append(f"실적 반응일({reaction_sym})")
    return {"session_date": d, "blackout": blackout, "explicit": explicit,
            "earnings_reaction": reaction_sym, "reason": " · ".join(reasons) or None}


def c5_direction(prev_close: Optional[float], price: Optional[float],
                 min_gap_pct: float = 0.0) -> Dict[str, Any]:
    """10:00 ET 가격 vs 전일 종가 부호 → 방향.

    위면 정방향(SOXL·TQQQ) 매수, 아래면 인버스(SOXS·SQQQ) 매수. 같으면 SHORT
    (연구 시뮬레이터의 `1 if o > prev_close else -1`와 동일 규칙).
    """
    try:
        pc, px = float(prev_close or 0), float(price or 0)
    except (TypeError, ValueError):
        return {"direction": None, "reason": "전일 종가/현재가 미확인"}
    if pc <= 0 or px <= 0:
        return {"direction": None, "reason": "전일 종가/현재가 미확인"}
    gap = (px / pc - 1.0) * 100.0
    if abs(gap) < float(min_gap_pct):
        return {"direction": None, "gap_pct": round(gap, 3), "prev_close": round(pc, 2),
                "price": round(px, 2),
                "reason": f"|갭| {abs(gap):.3f}% < 최소 {min_gap_pct}%"}
    direction = "LONG" if gap > 0 else "SHORT"
    return {"direction": direction, "gap_pct": round(gap, 3), "prev_close": round(pc, 2),
            "price": round(px, 2),
            "reason": f"10:00 ${px:.2f} {'>' if gap > 0 else '≤'} 전일 종가 ${pc:.2f} ({gap:+.2f}%)"}


def c5_open30_signal(prev_close: Optional[float], price: Optional[float],
                     atr_pct: Optional[float], session_date: Optional[str],
                     config: Optional[Dict[str, Any]] = None,
                     calendar: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """C5 신호 1건 — 방향·손절 폭·청산 시각·예산을 한 번에 돌려준다.

    실제 집행 여부(시각 창·1일 1회·티커 중복)는 `core/autotrader`가 판단하고,
    이 함수는 신호 자체만 계산한다(테스트 가능한 순수 함수).
    """
    cfg = c5_config(config)
    out: Dict[str, Any] = {"source": "C5_OPEN30_DIRECTION", "pilot": "C5",
                           "session_date": str(session_date or "")[:10],
                           "active": False, "budget_usd": float(cfg.get("budget_usd") or 150.0)}
    bo = c5_blackout(session_date, cfg, calendar)
    out["blackout"] = bo
    if not cfg.get("enabled", True):
        out["reason"] = "C5 파일럿 비활성"
        return out
    if bo["blackout"]:
        out["reason"] = f"진입 제외일 — {bo['reason']}"
        return out
    d = c5_direction(prev_close, price, float(cfg.get("min_gap_pct") or 0.0))
    out.update({"direction": d.get("direction"), "gap_pct": d.get("gap_pct"),
                "prev_close": d.get("prev_close"), "price": d.get("price")})
    if not d.get("direction"):
        out["reason"] = d.get("reason")
        return out
    atr = float(atr_pct or 0.0)
    tickers = cfg.get("long_tickers" if d["direction"] == "LONG" else "short_tickers") or []
    out.update({"tickers": list(tickers),
                "atr_pct": round(atr, 2) if atr > 0 else None,
                "exit_kst": cfg.get("exit_kst") or "05:40"})
    if atr <= 0:
        out["reason"] = "ATR 확인 불가"
        return out
    stop_pct = max(2.0, min(15.0, float(cfg.get("sl_atr_mult") or 3.0) * atr))
    out.update({"active": True, "stop_pct": round(stop_pct, 2),
                "reason": f"C5 {d['direction']} — {d['reason']}"})
    return out
