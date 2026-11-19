"""
Desk Lab — 검증 데스크(강우진·윤태호·임수아)가 쓰는 오프라인 실측 평가기.

왜 필요한가
-----------
'직원이 서로 업무 결과를 주고받으며 지속 빌드업'하려면, 리서치 데스크가 낸 가설을
검증 데스크가 **실제 가격 경로**로 재는 과정이 있어야 한다. 네트워크 없이 몇 초 안에
돌아야 10~30분 펄스에서 매번 쓸 수 있으므로, `data_cache/backtest_daily.pkl`
(2년 일봉: SOXL·TQQQ·SOXS·SQQQ …)을 읽어 일봉 단위로 진입/청산을 재구성한다.

무엇을 재는가 (규격을 실전과 맞춘다)
------------------------------------
`mode="swing"`  — 승인된 DIP_SWING_DAILY 규격(`core/bracket_specs`) 그대로:
                  진입 = 신호 다음 봉 시가, SL/TP = ATR×배수(3~15% / 3~20% 클램프),
                  최대 3세션 보유 후 종가 청산. 일봉으로 정확히 잴 수 있는 유일한 규격이다.
`mode="chandelier"` — 장중 규격을 일봉으로 근사한다(참고용, 게이트에 쓰지 않는다).
                  장중 ATR은 5분봉 기준이라 일봉 ATR로 대신하면 트레일 폭이 10배 넓어진다.

한계(그대로 보고한다)
---------------------
* 일봉만 보므로 장중 도달 순서를 모른다. 같은 봉에서 SL·TP가 모두 닿으면 **손절을 먼저**
  가정한다(최악 가정).
* 왕복 비용 0.24%(NH 0.09% + 슬리피지 0.15%)를 뺀 순수익률로만 비교한다.
* 장중 트레일(`trail_atr_mult`)은 이 도구로 검증하지 않는다 — `data/ab_20261118/*.json`의
  60분봉 A/B 결과가 그 파라미터의 근거다.

In-Sample / Out-of-Sample
-------------------------
기간을 70/30으로 자른다. 게이트는 **OOS만** 본다(`core/buildup.evaluate_candidate`).
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data_cache", "backtest_daily.pkl")
DAILY_UNIVERSE = ["SOXL", "TQQQ", "SOXS", "SQQQ", "QQQ", "^VIX"]
ROUND_TRIP_FEE_PCT = 0.24

# DIP_SWING_DAILY 규격(승인본) — 후보가 이 값을 바꿔 가며 측정된다.
SWING_SPEC: Dict[str, Any] = {
    "mode": "swing",
    "sl_atr_mult": 1.5, "sl_min_pct": 3.0, "sl_max_pct": 15.0,
    "tp_atr_mult": 1.5, "tp_min_pct": 3.0, "tp_max_pct": 20.0,
    "max_hold_sessions": 3,
}

DEFAULT_FILTERS: Dict[str, Any] = {"rsi2_max": 20.0, "pct_b_max": 0.15,
                                   "rvol_min": 1.0, "rvol_required": True}


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


_MEM_CACHE: Dict[str, Dict[str, Any]] = {}


def load_daily(force: bool = False, period: str = "2y") -> Dict[str, pd.DataFrame]:
    """일봉 캐시 로드. force=True면 네트워크로 새로 받는다(실패하면 기존 캐시 유지)."""
    if not force and _MEM_CACHE.get("frames"):
        return _MEM_CACHE["frames"]
    if force:
        try:
            from core import backtest_lab
            frames = backtest_lab.load_daily(period=period, force=True)
            if frames:
                _MEM_CACHE["frames"] = frames
                return frames
        except Exception:
            pass
    try:
        with open(CACHE, "rb") as f:
            obj = pickle.load(f)
        frames = obj.get("frames") or {}
        if frames:
            _MEM_CACHE["frames"] = frames
            return frames
    except Exception:
        pass
    return {}


def data_fingerprint(daily: Dict[str, pd.DataFrame]) -> str:
    """데이터가 바뀌었는지 판단하는 지문(마지막 날짜 + 종가들)."""
    parts = []
    for sym in sorted(daily):
        df = daily[sym]
        if df is None or len(df) == 0:
            continue
        try:
            last = df.index[-1]
            parts.append(f"{sym}:{pd.Timestamp(last).date()}:{float(df['Close'].iloc[-1]):.4f}:{len(df)}")
        except Exception:
            continue
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def data_as_of(daily: Dict[str, pd.DataFrame]) -> Optional[str]:
    dates = []
    for df in daily.values():
        try:
            dates.append(pd.Timestamp(df.index[-1]).date().isoformat())
        except Exception:
            continue
    return max(dates) if dates else None


# ------------------------------------------------------------------ indicators
def _rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(100.0)


def with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"]
    out["RSI2"] = _rsi(close, 2)
    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std(ddof=0)
    upper, lower = ma20 + 2 * sd20, ma20 - 2 * sd20
    width = (upper - lower).replace(0.0, np.nan)
    out["PCT_B"] = ((close - lower) / width).fillna(0.5)
    out["VOL20"] = out["Volume"].rolling(20).mean()
    out["RVOL"] = (out["Volume"] / out["VOL20"].replace(0.0, np.nan)).fillna(1.0)
    tr = pd.concat([out["High"] - out["Low"],
                    (out["High"] - close.shift()).abs(),
                    (out["Low"] - close.shift()).abs()], axis=1).max(axis=1)
    out["ATR"] = tr.rolling(14).mean()
    out["ATR_PCT"] = (out["ATR"] / close * 100.0).fillna(6.0)
    out["EMA20"] = close.ewm(span=20, adjust=False).mean()
    return out


# ------------------------------------------------------------------ simulation
def _entry_ok(row: pd.Series, flt: Dict[str, Any]) -> bool:
    try:
        if float(row["RSI2"]) > float(flt.get("rsi2_max", 20.0)):
            return False
        if float(row["PCT_B"]) > float(flt.get("pct_b_max", 0.15)):
            return False
        rvol = float(row["RVOL"])
        if flt.get("rvol_required", True):
            if rvol < float(flt.get("rvol_min", 1.0)):
                return False
        elif rvol < float(flt.get("rvol_min", 1.0)) and rvol < 1.0:
            return False      # 가점 모드에서도 RVOL 1.0 미만이면 진입하지 않는다(보수적)
        return True
    except Exception:
        return False


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(x)))


def simulate(daily: Dict[str, pd.DataFrame], params: Optional[Dict[str, Any]] = None,
             spec: Optional[Dict[str, Any]] = None,
             tickers: Tuple[str, ...] = ("SOXL", "TQQQ"),
             start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    """일봉 경로로 진입·청산을 재구성한다."""
    p = dict(spec or SWING_SPEC)
    for k, v in (spec or {}).items():
        p[k] = v
    for k in ("sl_atr_mult", "tp_atr_mult", "sl_min_pct", "sl_max_pct", "tp_min_pct", "tp_max_pct",
              "max_hold_sessions", "trail_atr_mult", "be_after_atr", "breakeven_pct"):
        if params and k in params:
            p[k] = params[k]
    flt = dict(DEFAULT_FILTERS)
    flt.update((params or {}).get("entry_filters") or {})
    mode = str(p.get("mode") or "swing")
    max_hold = max(1, int(p.get("max_hold_sessions", 3)))

    trades: List[Dict[str, Any]] = []
    for sym in tickers:
        df = daily.get(sym)
        if df is None or len(df) < 40:
            continue
        d = with_indicators(df)
        if start:
            d = d[d.index >= pd.Timestamp(start)]
        if end:
            d = d[d.index <= pd.Timestamp(end)]
        if len(d) < 30:
            continue
        idx = list(d.index)
        i = 20
        while i < len(idx) - 1:
            row = d.iloc[i]
            if not _entry_ok(row, flt):
                i += 1
                continue
            entry = float(d.iloc[i + 1]["Open"])
            if entry <= 0:
                i += 1
                continue
            atr_pct = float(row["ATR_PCT"]) or 6.0
            atr_abs = entry * atr_pct / 100.0
            sl_pct = _clamp(float(p.get("sl_atr_mult", 1.5)) * atr_pct,
                            p.get("sl_min_pct", 3.0), p.get("sl_max_pct", 15.0))
            sl = entry * (1 - sl_pct / 100.0)
            tp = None
            if p.get("tp_atr_mult") is not None:
                tp_pct = _clamp(float(p["tp_atr_mult"]) * atr_pct,
                                p.get("tp_min_pct", 3.0), p.get("tp_max_pct", 20.0))
                tp = entry * (1 + tp_pct / 100.0)
            k = float(p.get("trail_atr_mult", 0.9))
            be_after = float(p.get("be_after_atr", 1.0)) * atr_abs
            be_floor = entry * (1 + float(p.get("breakeven_pct", 0.34)) / 100.0)
            hh = entry
            exit_px = reason = exit_i = None
            for j in range(i + 1, min(len(idx), i + 1 + max_hold)):
                bar = d.iloc[j]
                lo, hi, close = float(bar["Low"]), float(bar["High"]), float(bar["Close"])
                hh = max(hh, hi)
                trail = hh - k * atr_abs
                if hh >= entry + be_after:
                    trail = max(trail, be_floor)
                stop = max(sl, trail) if mode == "chandelier" else sl
                hit_stop = lo <= stop
                hit_tp = tp is not None and hi >= tp
                if hit_stop and hit_tp:
                    exit_px, reason, exit_i = stop, "STOP/TRAIL", j      # 최악 가정
                    break
                if hit_stop:
                    exit_px, reason, exit_i = stop, "STOP/TRAIL", j
                    break
                if hit_tp:
                    exit_px, reason, exit_i = tp, "TARGET", j
                    break
                if (j - (i + 1) + 1) >= max_hold:
                    exit_px, reason, exit_i = close, "TIME", j
                    break
            if exit_px is None:
                j = min(len(idx) - 1, i + max_hold)
                exit_px, reason, exit_i = float(d.iloc[j]["Close"]), "TIME", j
            pct_net = (exit_px / entry - 1.0) * 100.0 - ROUND_TRIP_FEE_PCT
            trades.append({
                "ticker": sym,
                "entry_date": pd.Timestamp(idx[i + 1]).date().isoformat(),
                "exit_date": pd.Timestamp(idx[exit_i]).date().isoformat(),
                "entry": round(entry, 3), "exit": round(exit_px, 3),
                "pct_net": round(pct_net, 3), "kind": reason,
                "atr_pct": round(atr_pct, 2), "sl_pct": round(sl_pct, 2),
            })
            i = exit_i + 1
    trades.sort(key=lambda t: t["entry_date"])
    return {"trades": trades, "stats": stats_of(trades), "spec": p, "filters": flt}


def stats_of(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if not n:
        return {"n": 0, "available": False}
    pcts = [float(t.get("pct_net") or 0.0) for t in trades]
    wins = [x for x in pcts if x > 0]
    losses = [x for x in pcts if x <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    eq = peak = mdd = 0.0
    for x in pcts:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    mean = sum(pcts) / n
    sd = (sum((x - mean) ** 2 for x in pcts) / max(1, n - 1)) ** 0.5
    return {
        "n": n, "available": True,
        "ev_pct": round(mean, 3),
        "win_rate_pct": round(len(wins) / n * 100.0, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "mdd_pct": round(mdd, 2),
        "best_pct": round(max(pcts), 2), "worst_pct": round(min(pcts), 2),
        "total_pct": round(sum(pcts), 2),
        "t_stat": round(mean / (sd / (n ** 0.5)), 2) if sd > 0 and n > 1 else None,
        "stop_share_pct": round(len([t for t in trades if t.get("kind") == "STOP/TRAIL"]) / n * 100.0, 1),
        "target_share_pct": round(len([t for t in trades if t.get("kind") == "TARGET"]) / n * 100.0, 1),
    }


def split_is_oos(trades: List[Dict[str, Any]], is_frac: float = 0.7) -> Dict[str, Any]:
    """기간 기준 70/30 분할 — 같은 날짜 경계를 모든 변형에 동일하게 적용한다."""
    if not trades:
        return {"is": [], "oos": [], "cut_date": None}
    dates = sorted({t["entry_date"] for t in trades})
    cut = dates[max(0, int(len(dates) * is_frac) - 1)] if len(dates) > 1 else dates[0]
    return {"is": [t for t in trades if t["entry_date"] <= cut],
            "oos": [t for t in trades if t["entry_date"] > cut],
            "cut_date": cut}


def evaluate_variant(daily: Dict[str, pd.DataFrame], candidate: Dict[str, Any],
                     baseline: Optional[Dict[str, Any]] = None,
                     tickers: Tuple[str, ...] = ("SOXL", "TQQQ")) -> Dict[str, Any]:
    """후보 vs 현행을 같은 데이터·같은 분할로 비교한다(게이트 판정용 원자료).

    `candidate`는 `{spec:{...}, entry_filters:{...}}` 형태이며, 빠진 키는 현행 값을 쓴다.
    """
    base_spec = dict(SWING_SPEC)
    base_filters = dict(DEFAULT_FILTERS)
    base_filters.update((baseline or {}).get("entry_filters") or {})
    base = simulate(daily, {"entry_filters": base_filters}, dict(base_spec), tickers=tickers)

    cand_spec = dict(base_spec)
    cand_spec.update((candidate or {}).get("spec") or {})
    cand_filters = dict(base_filters)
    cand_filters.update((candidate or {}).get("entry_filters") or {})
    cand = simulate(daily, {"entry_filters": cand_filters}, cand_spec, tickers=tickers)

    cut = split_is_oos(base.get("trades") or []).get("cut_date")

    def _split(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not cut:
            st = stats_of(rows)
            return st, st
        return (stats_of([t for t in rows if t["entry_date"] <= cut]),
                stats_of([t for t in rows if t["entry_date"] > cut]))

    base_is, base_oos = _split(base.get("trades") or [])
    cand_is, cand_oos = _split(cand.get("trades") or [])

    return {
        "evaluated_at_kst": _now_kst(),
        "data_as_of": data_as_of(daily),
        "data_fingerprint": data_fingerprint(daily),
        "cut_date": cut,
        "baseline": {"all": base["stats"], "is": base_is, "oos": base_oos},
        "candidate": {"all": cand["stats"], "is": cand_is, "oos": cand_oos},
        "delta_oos_ev_pct": round(float(cand_oos.get("ev_pct") or 0.0) - float(base_oos.get("ev_pct") or 0.0), 3),
        "delta_oos_pf": (round(float(cand_oos.get("profit_factor") or 0.0) - float(base_oos.get("profit_factor") or 0.0), 2)
                         if cand_oos.get("profit_factor") and base_oos.get("profit_factor") else None),
        "mdd_worsening_pp": round(float(base_oos.get("mdd_pct") or 0.0) - float(cand_oos.get("mdd_pct") or 0.0), 2),
        "spec_candidate": {k: cand_spec.get(k) for k in SWING_SPEC},
        "filters_candidate": cand_filters,
        "note": ("일봉 경로 근사(같은 봉에서 SL·TP 동시 도달은 손절 우선) · 왕복 0.24% 차감 · "
                 "In-Sample=기간 앞 70%, Out-of-Sample=뒤 30% · DIP_SWING_DAILY 규격"),
    }


def summary_for_dashboard(daily: Optional[Dict[str, pd.DataFrame]] = None) -> Dict[str, Any]:
    daily = daily or load_daily()
    return {"data_as_of": data_as_of(daily), "data_fingerprint": data_fingerprint(daily),
            "tickers": [s for s in DAILY_UNIVERSE if s in daily] or sorted(daily)[:6],
            "round_trip_fee_pct": ROUND_TRIP_FEE_PCT,
            "spec": SWING_SPEC,
            "note": "검증 데스크 공용 실측 캐시(2년 일봉). 네트워크 없이 수 초 내 재계산."}


if __name__ == "__main__":
    import time
    t0 = time.time()
    frames = load_daily()
    print("data as_of", data_as_of(frames), "fp", data_fingerprint(frames), f"({time.time()-t0:.1f}s)")
    base = {"entry_filters": DEFAULT_FILTERS}
    a = simulate(frames, base)
    print("baseline", json.dumps(a["stats"], ensure_ascii=False))
    for cand in ({"spec": {"sl_atr_mult": 1.2}}, {"spec": {"sl_atr_mult": 1.8}},
                 {"spec": {"tp_atr_mult": 1.2}}, {"spec": {"tp_atr_mult": 2.0}},
                 {"spec": {"max_hold_sessions": 2}}, {"spec": {"max_hold_sessions": 4}},
                 {"entry_filters": {"rvol_min": 1.2}}, {"entry_filters": {"rsi2_max": 15.0}},
                 {"entry_filters": {"pct_b_max": 0.10}}):
        ev = evaluate_variant(frames, cand, base)
        c = ev["candidate"]
        print(f"{cand} dEV_oos {ev['delta_oos_ev_pct']:+.3f} | oos {ev['candidate']['oos'].get('n')}건 "
              f"EV {ev['candidate']['oos'].get('ev_pct')} PF {ev['candidate']['oos'].get('profit_factor')} "
              f"MDD {ev['candidate']['oos'].get('mdd_pct')} | all {c['all'].get('n')}건 "
              f"EV {c['all'].get('ev_pct')} PF {c['all'].get('profit_factor')} "
              f"(base {ev['baseline']['all'].get('ev_pct')}/{ev['baseline']['all'].get('profit_factor')})")
