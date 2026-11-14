#!/usr/bin/env python3
"""
LEI / Term-Spread filter defence test (Agent 7 input)
=====================================================

Question the brief asks Agent 7 to answer: does a leading-indicator / term-spread
filter actually defend the account? This script measures it instead of asserting it.

Test design (all inputs are real series, nothing simulated):
  * Underlying: SOXL 100% (and a 60/40 SOXL/TQQQ rotation variant) — the desk's
    live leveraged universe.
  * Filter states, evaluated on each day's close and applied from the next close:
      F1 term spread : risk-off while FRED T10Y3M < 0 (curve inverted)
      F2 trend       : risk-off while SPY < 200-day moving average
      F3 credit      : risk-off while FRED BAMLH0A0HYM2 (HY OAS) > 500bp
      F4 composite   : risk-off if ANY of F1/F2/F3 is off
                       (the three CUBE-LEI components that have a long public
                        history; Estrella & Mishkin 1998, Gilchrist & Zakrajšek
                        2012, Stock & Watson 1989)
    When risk-off the portfolio parks in SGOV (0-3M T-bill).
  * Costs: 0.24% charged on every switch (the desk's 왕복 friction convention).
  * Window: 5 years by default (captures the 2022 drawdown), configurable.

Output: data/lei_filter_backtest.json (read by Agent 7). Simulation only — never
mixed into the live ledger.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import leading_indicators, market_snapshot

OUT = os.path.join(BASE_DIR, "data", "lei_filter_backtest.json")
DOCS_OUT = os.path.join(BASE_DIR, "docs", "lei_filter_backtest.json")
FRICTION_PER_SWITCH = 0.0024  # 왕복 0.24% — 기존 backtest_lab과 동일 규약


def _max_dd(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min() * 100)


def _sharpe(daily_ret: pd.Series) -> float:
    r = daily_ret.dropna()
    if len(r) < 30 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(252))


def _metrics(equity: pd.Series, switches: int, invested_days: int, total_days: int) -> Dict[str, Any]:
    eq = equity.dropna()
    if len(eq) < 30:
        return {}
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = (float(eq.iloc[-1]) / float(eq.iloc[0]) - 1.0) * 100
    cagr = ((float(eq.iloc[-1]) / float(eq.iloc[0])) ** (1 / years) - 1) * 100
    mdd = _max_dd(eq)
    daily = eq.pct_change()
    return {
        "total_return_pct": round(total, 2),
        "cagr_pct": round(cagr, 2),
        "mdd_pct": round(mdd, 2),
        "sharpe": round(_sharpe(daily), 2),
        "calmar": round(cagr / abs(mdd), 2) if mdd else None,
        "switches": switches,
        "time_in_market_pct": round(invested_days / max(1, total_days) * 100, 1),
        "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
        "days": int(len(eq)),
    }


def _run(df: pd.DataFrame, weights: Dict[str, float], filter_on: pd.Series,
         cost: float = FRICTION_PER_SWITCH, off_scale: float = 0.0) -> Dict[str, Any]:
    """Backtest: full exposure while filter_on, `off_scale` exposure otherwise (rest in SGOV).

    off_scale=0.0 → binary switch to SGOV; off_scale=0.5 → half-size de-risking.
    """
    rets = pd.DataFrame(index=df.index)
    for sym, w in weights.items():
        rets[sym] = df[sym].pct_change() * w
    sgov = df["SGOV"].pct_change().fillna(0.0)
    strat = rets.sum(axis=1)

    state = filter_on.shift(1).fillna(False).astype(bool)  # act on next session
    exposure = np.where(state, 1.0, float(off_scale))
    daily = exposure * strat.fillna(0.0) + (1 - exposure) * sgov
    exp_s = pd.Series(exposure, index=df.index)
    switches = int((exp_s.diff().abs() > 1e-9).sum())
    equity = (1 + pd.Series(daily, index=df.index)).cumprod()
    equity = equity * (1 - cost) ** np.cumsum(np.abs(exp_s.diff().fillna(0)).values)
    m = _metrics(equity, switches, int(state.sum()), len(state))
    m["weights"] = weights
    m["avg_exposure_pct"] = round(float(exp_s.mean()) * 100, 1)
    return m


def main(force: bool = False) -> Dict[str, Any]:
    lei_hist = leading_indicators.load_long_history(["SOXL", "TQQQ", "SPY", "SGOV"], period="5y", force=force)
    need = ["SOXL", "TQQQ", "SPY", "SGOV"]
    missing = [s for s in need if s not in lei_hist or len(lei_hist[s]) < 500]
    if missing:
        # retry once without the cache
        lei_hist = leading_indicators.load_long_history(need, period="5y", force=True)

    df = pd.DataFrame({s: lei_hist[s]["Close"].astype(float) for s in need if s in lei_hist}).dropna()
    if len(df) < 500:
        raise SystemExit(f"가격 이력 부족: {len(df)}행 (최소 500 필요)")

    fred = market_snapshot.load_fred(series=["T10Y3M", "BAMLH0A0HYM2"], include_history=True, force=force)

    def series(sid: str) -> Optional[pd.Series]:
        d = fred.get(sid) or {}
        s = d.get("_series")
        return s if isinstance(s, pd.Series) else None

    t3m = series("T10Y3M")
    hy = series("BAMLH0A0HYM2")
    if t3m is None and hy is None:
        raise SystemExit("FRED 시계열 미확보 — 필터 백테스트 불가")

    idx = df.index
    curve_off = pd.Series(False, index=idx)
    if t3m is not None:
        curve_off = (t3m.reindex(t3m.index.union(idx)).ffill().reindex(idx) < 0).fillna(False)
    credit_off = pd.Series(False, index=idx)
    credit_z_off = pd.Series(False, index=idx)
    if hy is not None:
        hy_al = hy.reindex(hy.index.union(idx)).ffill().reindex(idx)
        credit_off = (hy_al > 5.0).fillna(False)
        # GZ-style relative measure: OAS one sigma above its 3y rolling mean
        roll_mean = hy_al.rolling(756, min_periods=250).mean()
        roll_std = hy_al.rolling(756, min_periods=250).std()
        credit_z_off = ((hy_al - roll_mean) / roll_std > 1.0).fillna(False)
    trend_off = (df["SPY"] < df["SPY"].rolling(200).mean()).fillna(False)
    composite_off = curve_off | credit_off | trend_off

    filters = {
        "NO_FILTER": pd.Series(True, index=idx),
        "F1_TERM_SPREAD": ~curve_off,
        "F2_SPY_200DMA": ~trend_off,
        "F3_CREDIT_OAS": ~credit_off,
        "F3B_CREDIT_OAS_Z": ~credit_z_off,
        "F4_COMPOSITE_LEI": ~composite_off,
    }
    portfolios = {
        "SOXL_100": {"SOXL": 1.0},
        "SOXL_TQQQ_60_40": {"SOXL": 0.6, "TQQQ": 0.4},
    }

    results: Dict[str, Any] = {}
    for pname, w in portfolios.items():
        results[pname] = {}
        for fname, f in filters.items():
            results[pname][fname] = _run(df, w, f)
        # partial de-risking: the sizing use of the same signal
        results[pname]["F4_COMPOSITE_LEI_HALFSIZE"] = _run(df, w, filters["F4_COMPOSITE_LEI"], off_scale=0.5)

    # defence effect vs no filter
    defence: List[Dict[str, Any]] = []
    for pname, rows in results.items():
        base = rows.get("NO_FILTER") or {}
        for fname, m in rows.items():
            if fname == "NO_FILTER" or not m:
                continue
            defence.append({
                "portfolio": pname, "filter": fname,
                "mdd_delta_pp": round(m["mdd_pct"] - (base.get("mdd_pct") or 0), 2),
                "sharpe_delta": round(m["sharpe"] - (base.get("sharpe") or 0), 2),
                "cagr_delta_pp": round(m["cagr_pct"] - (base.get("cagr_pct") or 0), 2),
                "calmar_delta": (round((m["calmar"] or 0) - (base.get("calmar") or 0), 2)
                                 if m.get("calmar") is not None and base.get("calmar") is not None else None),
                "verdict": ("MDD 축소+" if m["mdd_pct"] > (base.get("mdd_pct") or 0) else "MDD 동일/악화"),
            })

    best = None
    for row in defence:
        if row["portfolio"] == "SOXL_100" and row["filter"] == "F4_COMPOSITE_LEI_HALFSIZE":
            best = row
            break

    window = {"start": str(df.index[0].date()), "end": str(df.index[-1].date()), "days": int(len(df))}
    curve_inv_days = int(curve_off.sum())
    out = {
        "generated_at_kst": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
        "is_simulation": True,
        "disclaimer": "시뮬레이션입니다. 실전 원장과 분리되어 있으며 대시보드 실적 칸에 반영되지 않습니다.",
        "window": window,
        "friction": f"스위치마다 {FRICTION_PER_SWITCH*100:.2f}% 차감(왕복 규약)",
        "fill_model": "종가 평가·다음 거래일 상태 적용(룩어헤드 없음)",
        "filters": {
            "F1_TERM_SPREAD": "FRED T10Y3M < 0 (금리차 역전) 이면 SGOV 파킹 — Estrella & Mishkin (1998)",
            "F2_SPY_200DMA": "SPY < 200일 이동평균이면 SGOV 파킹 — Stock & Watson (1989) 주가 성분",
            "F3_CREDIT_OAS": "HY OAS > 500bp 이면 SGOV 파킹 — Gilchrist & Zakrajšek (2012) 신용경로",
            "F3B_CREDIT_OAS_Z": "HY OAS가 3년 롤링 평균 +1σ 초과면 SGOV 파킹(상대 신용스트레스)",
            "F4_COMPOSITE_LEI": "위 3개 중 하나라도 위험신호면 SGOV 파킹 — CUBE-LEI의 장기 시계열 확보 성분 조합",
            "F4_COMPOSITE_LEI_HALFSIZE": "같은 신호를 바이너리 청산 대신 50% 축소(사이징)로 적용",
        },
        "filter_state_days": {
            "curve_inverted_days": curve_inv_days,
            "credit_off_days": int(credit_off.sum()),
            "credit_z_off_days": int(credit_z_off.sum()),
            "hy_oas_max_pct_in_window": (round(float(hy.reindex(hy.index.union(idx)).ffill().reindex(idx).max()), 2)
                                         if hy is not None else None),
            "trend_off_days": int(trend_off.sum()),
            "composite_off_days": int(composite_off.sum()),
            "total_days": int(len(idx)),
        },
        "results": results,
        "defence_effect": defence,
        "headline": ({
            "portfolio": "SOXL_100", "filter": "F4_COMPOSITE_LEI_HALFSIZE",
            "mdd_pp": best["mdd_delta_pp"], "sharpe": best["sharpe_delta"], "cagr_pp": best["cagr_delta_pp"],
        } if best else None),
        "data_provenance": {
            "prices": "Yahoo Finance 5년 일봉 (SOXL/TQQQ/SPY/SGOV)",
            "macro": {k: {"latest": (fred.get(k) or {}).get("latest"), "date": (fred.get(k) or {}).get("date"),
                          "source": (fred.get(k) or {}).get("source")} for k in ("T10Y3M", "BAMLH0A0HYM2")},
            "hardcoded_values": False,
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    os.makedirs(os.path.dirname(DOCS_OUT), exist_ok=True)
    payload = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    for p in (OUT, DOCS_OUT):
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)

    print(f"✅ LEI filter backtest {window['start']}~{window['end']} ({window['days']}일)")
    for pname, rows in results.items():
        for fname, m in rows.items():
            print(f"  {pname:18s} {fname:18s} CAGR {m.get('cagr_pct'):>7.2f}%  MDD {m.get('mdd_pct'):>7.2f}%  "
                  f"Sharpe {m.get('sharpe'):>5.2f}  시장노출 {m.get('time_in_market_pct')}%")
    return out


if __name__ == "__main__":
    main(force="--force" in sys.argv)
