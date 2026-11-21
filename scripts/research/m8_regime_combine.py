#!/usr/bin/env python3
"""CUBE-M8 노출 계수 × V2 레짐 결합 백테스트 (2026-11-21).

무엇을 답하는가
---------------
① CUBE-M8을 shadow(계수 1.0) / linear / binary로 쓸 때, V2 레짐 모델
   (REGIME_ADAPTIVE_DIP_V1)의 OOS 성과(Sharpe·MDD·수익률·승률)가 어떻게 달라지는가.
② 레짐 게이트(CRISIS 신규 롱 금지)를 함께 켜면 더 나아지는가, 아니면 기회를 잃는가.

방법 (미래정보 차단)
--------------------
* 신호는 전일 완료봉, 진입은 다음 봉 시가(`core/regime_model.simulate` 규칙 그대로).
* M8 복합 z는 FRED 원계열(로컬 캐시)과 가격으로 `core/macro_leading.build_panel`을 다시
  계산한다 — 각 성분은 발표 지연(`align_availability`)을 거쳐 그 시점에 알 수 있던 값만 쓴다.
* 노출 계수는 **진입 상한과 리스크 예산에만** 곱한다(보유 포지션은 강제 축소하지 않는다).
* 비용은 왕복 0.24%(V2 기본)와 0.44%(프리장 가정) 둘 다 보고한다.

실행
----
    python3 scripts/research/m8_regime_combine.py [--fee 0.24]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from core import macro_leading, regime_model  # noqa: E402

RESEARCH = os.path.join(BASE_DIR, "data_cache", "research")
FRED_DIR = os.path.join(BASE_DIR, "data_cache", "fred")
OUT = os.path.join(BASE_DIR, "docs", "m8_regime_combine_20261121.json")

M8_PRICE_SYMBOLS = ["BTC-USD", "GC=F", "CL=F", "SPY", "QQQ", "SOXX", "SOXL", "TQQQ",
                    "SGOV", "BIL", "HYG", "IEF"]
KST = timedelta(hours=9)


def _now() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


def load_fred_series() -> Dict[str, pd.Series]:
    out: Dict[str, pd.Series] = {}
    for sid in macro_leading.FRED_IDS:
        path = os.path.join(FRED_DIR, f"{sid}.csv")
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path)
            col = [c for c in df.columns if c != "observation_date"][0]
            s = pd.Series(pd.to_numeric(df[col], errors="coerce").values,
                          index=pd.to_datetime(df["observation_date"]))
            s = s.dropna().sort_index()
            if len(s) > 24:
                out[sid] = s.astype(float)
        except Exception:
            continue
    return out


def _frame_from_price(sym: str) -> Optional[pd.DataFrame]:
    path = os.path.join(RESEARCH, f"{sym}_1d_5y.pkl")
    if os.path.exists(path):
        try:
            obj = pd.read_pickle(path)
            if isinstance(obj, dict) and "frames" in obj:
                obj = obj["frames"].get(sym)
            if isinstance(obj, pd.DataFrame) and len(obj):
                return obj[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            pass
    try:
        import yfinance as yf
        dl = yf.download(sym, period="5y", interval="1d", progress=False,
                         auto_adjust=False, threads=False)
        if dl is None or len(dl) == 0:
            return None
        if isinstance(dl.columns, pd.MultiIndex):
            dl.columns = [c[0] for c in dl.columns]
        df = dl[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df):
            df.to_pickle(path)
        return df if len(df) else None
    except Exception:
        return None


def load_prices() -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}
    for sym in M8_PRICE_SYMBOLS:
        f = _frame_from_price(sym)
        if f is not None:
            frames[sym] = f
    return frames


def m8_series(calendar: pd.DatetimeIndex, prices: Dict[str, pd.DataFrame],
              fred: Dict[str, pd.Series]) -> Dict[str, Any]:
    raw = {"fred": fred, "prices": prices}
    panel = macro_leading.build_panel(raw, calendar=calendar)
    comp = panel.get("composite")
    return {"composite": comp, "members": {k: bool(v.get("available"))
                                          for k, v in (panel.get("members") or {}).items()}}


def scale_series(comp: pd.Series, mode: str) -> pd.Series:
    """M8 복합 z → 날짜별 노출 계수. shadow=1.0 · linear=clip(0.75+0.25z,0.35,1.0) · binary=z>0."""
    if mode == "shadow":
        return pd.Series(1.0, index=comp.index)
    if mode == "binary":
        return comp.apply(lambda z: 1.0 if pd.notna(z) and z > 0 else 0.0)
    params = {"intercept": 0.75, "slope": 0.25, "floor": 0.35, "cap": 1.0}
    return comp.apply(lambda z: macro_leading.linear_scale(None if pd.isna(z) else float(z), params))


def _exposure_map(scales: pd.Series) -> Dict[str, float]:
    return {str(pd.Timestamp(i).date()): float(v) for i, v in scales.items() if pd.notna(v)}


def run_variant(frames: Dict[str, pd.DataFrame], params: Dict[str, Any], fee: float,
                start: str, end: str, exposure: Optional[Dict[str, float]]) -> Dict[str, Any]:
    p = dict(params)
    p["fee_round_trip_pct"] = fee
    res = regime_model.simulate(frames, p, start=start, end=end, exposure_by_date=exposure)
    port = res.get("portfolio") or {}
    long_stats = res.get("long_stats") or {}
    return {
        "n_trades": long_stats.get("n"),
        "ev_pct": long_stats.get("ev_pct"),
        "win_rate_pct": long_stats.get("win_rate_pct"),
        "profit_factor": long_stats.get("profit_factor"),
        "total_return_pct": port.get("total_return_pct"),
        "sharpe": port.get("sharpe"),
        "mdd_pct": port.get("mdd_pct"),
        "vol_pct": port.get("vol_pct"),
        "vetoes": res.get("vetoes"),
        "exposure_overlay": res.get("exposure_overlay"),
        "regime_days": res.get("regime_days"),
    }


VARIANTS: Dict[str, Dict[str, Any]] = {
    "V1_baseline_dip": {"multi_confirmation": False, "regime_gate": False},
    "V2_regime": {"multi_confirmation": True, "regime_gate": False},
    "V2_plus_crisis_ban": {"multi_confirmation": True, "regime_gate": True},
}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.24)
    ap.add_argument("--start", default="2022-01-03")
    ap.add_argument("--cut", default="2025-06-30")
    ap.add_argument("--end", default="2026-11-17")
    args = ap.parse_args(argv)

    frames = regime_model.load_frames("5y")
    if not frames:
        print("일봉 캐시가 없습니다")
        return 1
    prices = load_prices()
    calendar = sorted(frames["SPY"].index)
    fred = load_fred_series()
    m8 = m8_series(pd.DatetimeIndex(calendar), prices, fred)
    comp = m8["composite"].reindex(pd.DatetimeIndex(calendar))
    coverage = int(comp.notna().sum())
    print(f"[m8] 복합 z 계산 {len(comp)}거래일 중 {coverage}일 유효 · 성분 "
          f"{sum(m8['members'].values())}/{len(m8['members'])}")

    scales = {mode: scale_series(comp, mode) for mode in ("shadow", "linear", "binary")}
    exposures = {mode: _exposure_map(s) for mode, s in scales.items()}

    windows = {"IS": (args.start, args.cut), "OOS": (args.cut, args.end), "FULL": (args.start, args.end)}
    results: Dict[str, Any] = {}
    for vname, overrides in VARIANTS.items():
        params = dict(regime_model.BASE_PARAMS)
        params.update(overrides)
        for m8mode in ("shadow", "linear", "binary"):
            key = f"{vname}|M8_{m8mode}"
            row = {"variant": vname, "m8": m8mode, "params": {k: params.get(k) for k in
                                                              ("multi_confirmation", "regime_gate",
                                                               "risk_per_trade_pct", "sl_max_pct")}}
            exp = None if m8mode == "shadow" else exposures[m8mode]
            for wname, (s, e) in windows.items():
                row[wname] = run_variant(frames, params, args.fee, s, e, exp)
            results[key] = row
            o = row["OOS"]
            print(f"{key:38s} OOS 수익 {o['total_return_pct']:>8}% Sharpe {o['sharpe']:>5} "
                  f"MDD {o['mdd_pct']:>7}% 승률 {o['win_rate_pct']:>5}% n {o['n_trades']} "
                  f"EV {o['ev_pct']}%")

    doc = {
        "generated_at_kst": _now(), "engine": "M8_exposure_x_V2_regime",
        "fee_round_trip_pct": args.fee,
        "windows": {k: {"start": v[0], "end": v[1]} for k, v in windows.items()},
        "m8": {"coverage_days": coverage, "total_days": int(len(comp)),
               "members_available": m8["members"],
               "scale_stats": {m: {"mean": round(float(s.mean()), 3),
                                   "min": round(float(s.min()), 3),
                                   "max": round(float(s.max()), 3),
                                   "zero_days": int((s <= 0).sum())}
                               for m, s in scales.items()}},
        "results": results,
        "note": ("V2 레짐 모델(core/regime_model.py) 일봉 시뮬레이션에 CUBE-M8 노출 계수를 "
                 "결합. 신호는 전일 완료봉·진입은 다음 봉 시가, 같은 봉 SL·TP 동시 도달은 손절 우선. "
                 "IS/OOS는 각 구간을 독립 실행한 성과(계좌를 구간 시작에 새로 시작)."),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"저장: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
