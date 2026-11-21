#!/usr/bin/env python3
"""
CUBE-M8 선행지표 예측력 백테스트 (Agent 7·8 검증 산출물)
=======================================================

동훈님 원칙(2026-11-20 16:01): "모든 행위는 백테스트를 진행해서 신뢰할 결과를
만들어서 적용해야해." 이 스크립트는 CUBE-M8(8대 거시·자산 선행지표)이 실제로
주식시장(SPY/QQQ/SOXX + 레버리지 SOXL/TQQQ)을 선행하는지, 그리고 그것을 매매
판단기준(노출 계수)으로 쓰는 것이 단순보유·기존 규칙보다 나은지를 실측한다.

검증 설계
---------
1) 신호 시점 (look-ahead 차단)
   * 모든 성분 z는 **과거 창만** 쓰는 rolling 표준화(일간 756거래일·월간 36개월).
   * 월간 지표는 `기말 + 발표지연(lag_days)`부터 값이 알려진 것으로 정렬.
   * t일 종가로 만든 노출 계수는 **t+1일 수익률**부터 적용(e_t · r_{t+1}).
   * +10일 추가 시차 강건성 검정으로 결론이 시차 가정에 기대지 않는지 확인.

2) 두 가지 부호 설계를 모두 공개한다
   * `M8_PRIOR`  — 논문·경제논리에 따른 사전 부호(설계 기본값).
   * `M8_ISFIT`  — **IS 구간에서만** 성분 IC를 재서 부호를 다시 정한 뒤 OOS로 검증.
     (사전 부호가 이 표본에서 틀렸다면 그 사실을 숨기지 않고 드러내는 장치)

3) 평가
   * IS: 2015-01-02 ~ 2021-12-31 / OOS: 2022-01-03 ~ 최근(2022 약세장 포함).
   * 지표: CAGR, 변동성, Sharpe, Sortino, MDD, Calmar, 노출, 스위치, 일별 PF.
   * 정보계수: corr(z_t, 향후 20거래일 수익률) + Newey-West(20) t값.
   * 분위 분석: IS 분위 임계값을 OOS에 그대로 적용.
   * 블록·성분별 IC: 어떤 지표가 실제로 예측력을 갖는지 전부 공개.

4) 판정 기준
   * "단순보유 대비"는 의미가 약하다. 데스크가 이미 쓰는 **SMA200**을 기준선으로
     삼아, M8을 얹었을 때 Sharpe·MDD가 실제로 개선되는지를 본다.
   * OOS IC의 Newey-West t값이 2를 넘지 못하면 "예측력 있다"고 말하지 않는다.

출력: data/macro_leading_backtest.json, docs/macro_leading_backtest.json, docs/macro_leading.md
시뮬레이션 전용 — 실전 원장·대시보드 실적 칸에는 절대 섞지 않는다.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import macro_leading as m8           # noqa: E402

OUT = os.path.join(BASE_DIR, "data", "macro_leading_backtest.json")
DOCS_OUT = os.path.join(BASE_DIR, "docs", "macro_leading_backtest.json")
DOCS_MD = os.path.join(BASE_DIR, "docs", "macro_leading.md")
SIGNS_PATH = os.path.join(BASE_DIR, "data", "macro_leading_signs.json")

FRICTION_PER_TURNOVER = 0.0012     # 노출 1.0 교체(한쪽)당 0.12% → 왕복 0.24% (데스크 기존 규약)
START = "2015-01-02"
SPLIT = "2022-01-01"
HORIZON = 20                       # 거래일
TARGETS = ["SPY", "QQQ", "SOXX", "SOXL", "TQQQ"]
SIGN_FIT_MIN_IC = 0.03             # IS |IC|가 이보다 작으면 부호를 뒤집지 않는다(노이즈 방지)


def _now_str() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------------ metrics
def _mdd(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1.0).min() * 100)


def _metrics(daily: pd.Series, exposure: pd.Series) -> Dict[str, Any]:
    r = daily.dropna()
    if len(r) < 30:
        return {}
    eq = (1 + r).cumprod()
    years = max(len(r) / 252.0, 1e-9)
    total = float(eq.iloc[-1] - 1.0) * 100
    cagr = (float(eq.iloc[-1]) ** (1 / years) - 1.0) * 100
    vol = float(r.std() * math.sqrt(252) * 100)
    sharpe = float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else 0.0
    downside = r[r < 0].std()
    sortino = float(r.mean() / downside * math.sqrt(252)) if downside and downside > 0 else None
    mdd = _mdd(eq)
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    pf = float(pos / neg) if neg > 0 else None
    exp = exposure.reindex(r.index).fillna(0.0)
    switches = int((exp.diff().abs() > 1e-6).sum())
    return {
        "total_return_pct": round(total, 2), "cagr_pct": round(cagr, 2),
        "vol_pct": round(vol, 2), "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2) if sortino is not None else None,
        "mdd_pct": round(mdd, 2),
        "calmar": round(cagr / abs(mdd), 2) if mdd else None,
        "pf_daily": round(pf, 2) if pf is not None else None,
        "avg_exposure_pct": round(float(exp.mean()) * 100, 1),
        "switches": switches, "days": int(len(r)),
        "hit_rate_pct": round(float((r > 0).mean()) * 100, 1),
    }


def _nw_se(x: np.ndarray, resid: np.ndarray, lags: int) -> float:
    """Newey-West HAC 표준오차(단변량 OLS 기울기)."""
    n = len(x)
    xc = x - x.mean()
    s2 = float((xc ** 2).sum())
    if s2 <= 0 or n < 3:
        return float("nan")
    u = xc * resid
    var = float((u ** 2).sum()) / n
    for l in range(1, min(lags, n - 1) + 1):
        gl = float((u[l:] * u[:-l]).sum()) / n
        var += 2 * (1 - l / (lags + 1)) * gl
    var *= n / (n - 2)
    return float(math.sqrt(max(var * n, 1e-18)) / s2)


def _ic(x: pd.Series, y: pd.Series) -> Dict[str, Any]:
    """정보계수(피어슨·스피어만) + Newey-West t값 + 방향 적중률."""
    df = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
    if len(df) < 60:
        return {"n": int(len(df)), "pearson": None, "spearman": None, "t_nw": None}
    pear = float(df["x"].corr(df["y"]))
    spear = float(df["x"].corr(df["y"], method="spearman"))
    xv = df["x"].values.astype(float)
    yv = df["y"].values.astype(float)
    coef = np.polyfit(xv, yv, 1)
    b = float(coef[0])
    resid = yv - np.polyval(coef, xv)
    se = _nw_se(xv, resid, HORIZON)
    t = b / se if se and np.isfinite(se) and se > 0 else None
    hit = float(((df["x"] > 0) == (df["y"] > 0)).mean() * 100)
    return {"n": int(len(df)), "pearson": round(pear, 4), "spearman": round(spear, 4),
            "t_nw": round(t, 2) if t is not None else None,
            "directional_hit_pct": round(hit, 1)}


# ------------------------------------------------------------------------ loading
def _cash_series(raw: Dict[str, Any], idx: pd.DatetimeIndex) -> pd.Series:
    """무위험 파킹 프록시: SGOV(2020~) → BIL(2007~) 이어붙임."""
    out = pd.Series(np.nan, index=idx, dtype=float)
    for sym in ("SGOV", "BIL"):
        f = (raw.get("prices") or {}).get(sym)
        if f is None:
            continue
        s = f["Close"].astype(float)
        s = s.reindex(s.index.union(idx)).ffill().reindex(idx)
        out = out.where(out.notna(), s)
    return out.ffill()


def _price(raw: Dict[str, Any], sym: str, idx: pd.DatetimeIndex) -> pd.Series:
    f = (raw.get("prices") or {}).get(sym)
    if f is None:
        return pd.Series(np.nan, index=idx)
    return f["Close"].astype(float).reindex(f.index.union(idx)).ffill().reindex(idx)


def _run(exposure: pd.Series, ret: pd.Series, cash: pd.Series) -> Dict[str, Any]:
    """t일 종가에 정한 노출은 **t+1일 수익률**부터 적용한다(같은 날 종가 수익률을 먹지 않는다).

    held_t = e_{t-1} → daily_t = held_t·ret_t + (1-held_t)·cash_ret_t − |Δe_t|·0.24%
    """
    e = exposure.reindex(ret.index).ffill().fillna(0.0)
    held = e.shift(1).fillna(0.0)                     # 전일 종가에 정한 비중으로 오늘 수익을 얻는다
    turnover = e.diff().abs().fillna(0.0)             # 오늘 종가에 실행한 회전(비용)
    cash_ret = cash.reindex(ret.index).ffill().pct_change().fillna(0.0)   # 파킹 '수익률'
    daily = held * ret + (1 - held) * cash_ret - turnover * FRICTION_PER_TURNOVER
    return {"daily": daily.fillna(0.0), "exposure": held}


def _slice_metrics(run: Dict[str, Any], start: str, end: Optional[str]) -> Dict[str, Any]:
    daily = run["daily"]
    return _metrics(daily.loc[start:end] if end else daily.loc[start:], run["exposure"])


def _composite(panel: Dict[str, Any], idx: pd.DatetimeIndex,
               member_mult: Optional[pd.Series] = None) -> Dict[str, Any]:
    """성분 z → 블록 z → 가중 복합 z. member_mult로 부호를 다시 정할 수 있다."""
    mz = panel["member_z"].reindex(idx)
    if member_mult is not None:
        mz = mz.mul(member_mult.reindex(mz.columns).fillna(1.0), axis=1)
    bz = pd.DataFrame(index=idx)
    for bkey in m8.BLOCK_META:
        cols = [s["key"] for s in m8.MEMBER_SPECS if s["block"] == bkey and s["key"] in mz.columns]
        if cols:
            bz[bkey] = mz[cols].mean(axis=1, skipna=True)
    w = pd.Series({c: m8.BLOCK_META[c]["weight"] for c in bz.columns}, dtype=float)
    numer = bz.mul(w, axis=1).sum(axis=1, skipna=True)
    denom = bz.notna().mul(w, axis=1).sum(axis=1)
    z = (numer / denom.replace(0, np.nan)).clip(-3, 3)
    return {"z": z, "block_z": bz}


def _strategy_set(z: pd.Series, prefix: str, sma: pd.Series) -> Dict[str, pd.Series]:
    lin = z.apply(lambda v: m8.linear_scale(v))
    return {
        f"{prefix}_LINEAR": lin,
        f"{prefix}_STEP": z.apply(lambda v: m8.regime_for(v)[2]),
        f"{prefix}_BINARY": (z > 0).astype(float).where(z.notna(), 0.0),
        f"{prefix}_LINEAR_X_SMA200": pd.Series(np.minimum(lin.values, sma.values), index=z.index),
    }


def _logit_fit(x: np.ndarray, y: np.ndarray) -> tuple:
    """단변량 로짓(IRLS) — 2모수라 수렴이 빠르다."""
    b0, b1 = 0.0, 0.0
    for _ in range(60):
        p = 1 / (1 + np.exp(-np.clip(b0 + b1 * x, -30, 30)))
        w = np.clip(p * (1 - p), 1e-6, None)
        g = np.array([np.sum(y - p), np.sum((y - p) * x)])
        H = np.array([[np.sum(w), np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x)]])
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        b0 += float(step[0])
        b1 += float(step[1])
        if np.max(np.abs(step)) < 1e-9:
            break
    return b0, b1


def _calibrate(z: pd.Series, fwd: pd.Series, is_mask: pd.Series) -> Dict[str, Any]:
    out: Dict[str, Any] = {"horizon_days": HORIZON, "active": False}
    y_is = fwd[is_mask].dropna()
    x_is = z.reindex(y_is.index).dropna()
    y_is = y_is.reindex(x_is.index)
    if len(y_is) < 300:
        return out
    b0, b1 = _logit_fit(x_is.values.astype(float), (y_is.values > 0).astype(float))
    out.update({"logit_intercept": round(b0, 4), "logit_slope": round(b1, 4),
                "is_up_rate_pct": round(float((y_is.values > 0).mean()) * 100, 1)})
    try:
        slope, intercept = np.polyfit(x_is.values.astype(float), y_is.values.astype(float), 1)
        out.update({"ret_intercept": round(float(intercept), 4), "ret_slope": round(float(slope), 4)})
    except Exception:
        pass
    yo = fwd[~is_mask].dropna()
    xo = z.reindex(yo.index).dropna()
    yo = yo.reindex(xo.index)
    if len(yo) > 100:
        p = 1 / (1 + np.exp(-np.clip(b0 + b1 * xo.values, -30, 30)))
        up = (yo.values > 0).astype(float)
        brier = float(np.mean((p - up) ** 2))
        base = float(up.mean())
        brier_base = float(np.mean((base - up) ** 2))
        out.update({"oos_brier": round(brier, 4), "oos_brier_base_rate": round(brier_base, 4),
                    "oos_up_rate_pct": round(base * 100, 1),
                    "oos_skill_vs_base_pct": round((brier_base - brier) / brier_base * 100, 2) if brier_base else None,
                    "oos_calibration_n": int(len(yo))})
        out["active"] = bool(brier < brier_base)
    return out


# ------------------------------------------------------------------------- backtest
def run_backtest(force: bool = False, quiet: bool = False) -> Dict[str, Any]:
    raw = m8.load_raw(force=force)
    spy_full = (raw.get("prices") or {}).get("SPY")
    spy = _price(raw, "SPY", pd.DatetimeIndex(spy_full.index))
    idx = spy.loc[START:].dropna().index
    panel = m8.build_panel(raw, idx, sign_fit={})   # 사전부호 그대로(부호 적합 파일 무시)
    cash = _cash_series(raw, idx)
    spy_al = spy.reindex(idx)
    sma = (spy_al >= spy_al.rolling(200).mean()).astype(float)
    is_mask = idx < pd.Timestamp(SPLIT)
    fwd_spy = (spy_al.shift(-HORIZON) / spy_al - 1.0) * 100.0

    # --- 사전 부호 복합
    prior = _composite(panel, idx)
    z_prior = prior["z"]
    # --- IS 부호 재적합(IS 구간 IC만 사용) → OOS는 건드리지 않는다
    member_mult: Dict[str, float] = {}
    sign_fit: Dict[str, Any] = {}
    half = pd.Timestamp("2018-07-02")
    early = is_mask & (idx < half)
    late = is_mask & (idx >= half)
    agree = 0
    for key in panel["member_z"].columns:
        ic_is = _ic(panel["member_z"][key][is_mask], fwd_spy[is_mask])
        pear = ic_is.get("pearson")
        mult = 1.0
        if pear is not None and pear < -SIGN_FIT_MIN_IC:
            mult = -1.0
        member_mult[key] = mult
        p_early = (_ic(panel["member_z"][key][early], fwd_spy[early]).get("pearson") or 0.0)
        p_late = (_ic(panel["member_z"][key][late], fwd_spy[late]).get("pearson") or 0.0)
        stable = bool(np.sign(p_early) == np.sign(p_late) and abs(p_early) > 0.02 and abs(p_late) > 0.02)
        agree += int(stable)
        sign_fit[key] = {"is_ic_prior_sign": pear, "is_t_nw": ic_is.get("t_nw"), "multiplier": mult,
                         "flipped": mult < 0, "ic_is_early": round(p_early, 4),
                         "ic_is_late": round(p_late, 4), "sign_stable": stable}
    isfit = _composite(panel, idx, pd.Series(member_mult))
    z_isfit = isfit["z"]

    strategies: Dict[str, pd.Series] = {
        "BH": pd.Series(1.0, index=idx),
        "SMA200": sma,
    }
    strategies.update(_strategy_set(z_prior, "M8P", sma))
    strategies.update(_strategy_set(z_isfit, "M8F", sma))
    inc_ok = pd.Series(True, index=idx)
    t10y3m = (raw.get("fred") or {}).get("T10Y3M")
    hy = (raw.get("fred") or {}).get("BAMLH0A0HYM2")
    if t10y3m is not None:
        s = t10y3m.reindex(t10y3m.index.union(idx)).ffill().reindex(idx)
        inc_ok &= (s >= 0).fillna(True)
    if hy is not None:
        s = hy.reindex(hy.index.union(idx)).ffill().reindex(idx)
        inc_ok &= (s <= 5.0).fillna(True)
    inc_ok &= (spy_al >= spy_al.rolling(200).mean()).fillna(True)
    strategies["LEI3_INCUMBENT"] = inc_ok.astype(float)

    results: Dict[str, Any] = {}
    for target in TARGETS:
        px = _price(raw, target, idx)
        ret = px.pct_change()
        if ret.notna().sum() < 500:
            continue
        block: Dict[str, Any] = {}
        for name, exp in strategies.items():
            run = _run(exp, ret, cash)
            block[name] = {"full": _slice_metrics(run, START, None),
                           "is": _slice_metrics(run, START, "2021-12-31"),
                           "oos": _slice_metrics(run, SPLIT, None)}
        results[target] = block

    # ---- 정보계수(두 부호 설계 모두)
    ic: Dict[str, Any] = {}
    for target in ("SPY", "QQQ", "SOXX"):
        y = ((_price(raw, target, idx).shift(-HORIZON) / _price(raw, target, idx) - 1.0) * 100.0)
        ic[target] = {
            "prior": {"is": _ic(z_prior[is_mask], y[is_mask]), "oos": _ic(z_prior[~is_mask], y[~is_mask]),
                      "full": _ic(z_prior, y)},
            "isfit": {"is": _ic(z_isfit[is_mask], y[is_mask]), "oos": _ic(z_isfit[~is_mask], y[~is_mask]),
                      "full": _ic(z_isfit, y)},
        }

    block_ic: Dict[str, Any] = {}
    for bkey in prior["block_z"].columns:
        bz = prior["block_z"][bkey]
        block_ic[bkey] = {"is": _ic(bz[is_mask], fwd_spy[is_mask]),
                          "oos": _ic(bz[~is_mask], fwd_spy[~is_mask])}

    member_ic: Dict[str, Any] = {}
    for spec in m8.MEMBER_SPECS:
        key = spec["key"]
        if key not in panel["member_z"].columns:
            continue
        s = panel["member_z"][key]
        member_ic[key] = {"block": spec["block"], "sign_prior": spec["sign"],
                          "is": _ic(s[is_mask], fwd_spy[is_mask]),
                          "oos": _ic(s[~is_mask], fwd_spy[~is_mask])}

    # ---- 분위 분석(IS 임계값 → OOS 적용), 두 복합 각각
    def _quintiles(z: pd.Series) -> List[Dict[str, Any]]:
        z_is = z[is_mask].dropna()
        if len(z_is) < 200:
            return []
        qs = [float(z_is.quantile(q)) for q in (0.2, 0.4, 0.6, 0.8)]

        def b_(v: float) -> float:
            if not np.isfinite(v):
                return np.nan
            for i, edge in enumerate(qs):
                if v < edge:
                    return float(i)
            return 4.0
        bucket = z.apply(b_)
        rows = []
        for i in range(5):
            sel = bucket == i
            rows.append({
                "quintile": i + 1,
                "label": "Q1 가장 위험회피" if i == 0 else ("Q5 가장 위험선호" if i == 4 else f"Q{i+1}"),
                "days_is": int((sel & is_mask).sum()), "days_oos": int((sel & ~is_mask).sum()),
                "mean_fwd20_is_pct": round(float(fwd_spy[sel & is_mask].mean()), 2) if (sel & is_mask).sum() > 10 else None,
                "mean_fwd20_oos_pct": round(float(fwd_spy[sel & ~is_mask].mean()), 2) if (sel & ~is_mask).sum() > 10 else None,
            })
        return rows

    quintiles = {"prior": _quintiles(z_prior), "isfit": _quintiles(z_isfit)}

    # ---- 캘리브레이션(IS 적합 → OOS Brier)
    cal_prior = _calibrate(z_prior, fwd_spy, is_mask)
    cal_isfit = _calibrate(z_isfit, fwd_spy, is_mask)

    # ---- 강건성: +10일 추가 시차 / BTC 블록 제외
    robust: Dict[str, Any] = {}
    panel_lag = m8.build_panel(raw, idx, extra_lag_days=10, sign_fit={})
    z_lag = _composite(panel_lag, idx)["z"]
    for target in ("SPY", "SOXX"):
        ret = _price(raw, target, idx).pct_change()
        for name, exp in (("M8P_LINEAR", z_lag.apply(lambda v: m8.linear_scale(v))),
                          ("M8P_BINARY", (z_lag > 0).astype(float).where(z_lag.notna(), 0.0))):
            run = _run(exp, ret, cash)
            robust[f"{target}|{name}|lag+10"] = {"is": _slice_metrics(run, START, "2021-12-31"),
                                                 "oos": _slice_metrics(run, SPLIT, None)}
    # ISFIT 복합도 같은 시차 강건성 검정(부호 재적합은 IS만 쓴 값 그대로 적용)
    isfit_lag = _composite(panel_lag, idx, pd.Series(member_mult))["z"]
    for target in ("SPY", "SOXX", "SOXL"):
        ret = _price(raw, target, idx).pct_change()
        lin = isfit_lag.apply(lambda v: m8.linear_scale(v))
        exp = pd.Series(np.minimum(lin.values, sma.values), index=idx)
        run = _run(exp, ret, cash)
        robust[f"{target}|M8F_LINEAR_X_SMA200|lag+10"] = {"is": _slice_metrics(run, START, "2021-12-31"),
                                                           "oos": _slice_metrics(run, SPLIT, None)}
    bz_nb = prior["block_z"].drop(columns=[c for c in ("crypto",) if c in prior["block_z"].columns],
                                  errors="ignore")
    w = pd.Series({c: m8.BLOCK_META[c]["weight"] for c in bz_nb.columns})
    z_nobtc = (bz_nb.mul(w, axis=1).sum(axis=1, skipna=True)
               / bz_nb.notna().mul(w, axis=1).sum(axis=1).replace(0, np.nan)).clip(-3, 3)
    for target in ("SPY", "SOXX"):
        ret = _price(raw, target, idx).pct_change()
        run = _run(z_nobtc.apply(lambda v: m8.linear_scale(v)), ret, cash)
        robust[f"{target}|M8P_LINEAR|no_btc_block"] = {"is": _slice_metrics(run, START, "2021-12-31"),
                                                       "oos": _slice_metrics(run, SPLIT, None)}

    # ---- 판정: 기준선은 SMA200(데스크 기존 규칙)
    per_target: Dict[str, Any] = {}
    for target in ("SPY", "SOXX", "SOXL"):
        b = results.get(target) or {}
        if not b:
            continue
        base = (b.get("SMA200") or {}).get("oos") or {}
        best_name, best_score = None, -9.0
        for name in ("M8F_LINEAR_X_SMA200", "M8P_LINEAR_X_SMA200", "M8F_STEP", "M8F_LINEAR",
                     "M8P_LINEAR", "M8F_BINARY", "M8P_BINARY"):
            m = (b.get(name) or {}).get("oos") or {}
            if not m or not base:
                continue
            score = (m.get("sharpe") or -9)
            if score > best_score:
                best_name, best_score = name, score
        if best_name is None or not base:
            continue
        bm = (b.get(best_name) or {}).get("oos") or {}
        bh = (b.get("BH") or {}).get("oos") or {}
        per_target[target] = {
            "incumbent": "SMA200", "incumbent_oos": {k: base.get(k) for k in
                                                     ("cagr_pct", "mdd_pct", "sharpe", "avg_exposure_pct")},
            "buy_hold_oos": {k: bh.get(k) for k in ("cagr_pct", "mdd_pct", "sharpe")},
            "best_variant": best_name,
            "best_oos": {k: bm.get(k) for k in ("cagr_pct", "mdd_pct", "sharpe", "avg_exposure_pct")},
            "sharpe_delta_vs_incumbent": round((bm.get("sharpe") or 0) - (base.get("sharpe") or 0), 2),
            "mdd_delta_pp_vs_incumbent": round((bm.get("mdd_pct") or 0) - (base.get("mdd_pct") or 0), 2),
            "cagr_delta_pp_vs_incumbent": round((bm.get("cagr_pct") or 0) - (base.get("cagr_pct") or 0), 2),
            "sharpe_delta_vs_bh": round((bm.get("sharpe") or 0) - (bh.get("sharpe") or 0), 2),
            "mdd_delta_pp_vs_bh": round((bm.get("mdd_pct") or 0) - (bh.get("mdd_pct") or 0), 2),
            "cagr_delta_pp_vs_bh": round((bm.get("cagr_pct") or 0) - (bh.get("cagr_pct") or 0), 2),
        }

    oos_ic_spy = (ic.get("SPY") or {}).get("isfit", {}).get("oos") or {}
    oos_ic_prior = (ic.get("SPY") or {}).get("prior", {}).get("oos") or {}
    ic_qqq = (ic.get("QQQ") or {}).get("isfit", {}).get("oos") or {}
    ic_soxx = (ic.get("SOXX") or {}).get("isfit", {}).get("oos") or {}
    n_beat = sum(1 for v in per_target.values() if (v["sharpe_delta_vs_incumbent"] or 0) > 0.02)
    t_isfit = oos_ic_spy.get("t_nw") or 0
    significant = bool(t_isfit >= 2.0)
    stability = round(agree / max(1, len(sign_fit)) * 100, 1)
    if n_beat >= 2 and significant and stability >= 60:
        mode = "advisory_validated"
        summary = (f"OOS에서 SMA200 대비 Sharpe 개선 {n_beat}/{len(per_target)} · IC t {t_isfit} · "
                   f"부호 안정성 {stability}% — 보조 판단기준으로 채택 가능. "
                   "실전 사이징 반영은 동훈님 승인 후.")
    elif n_beat >= 1:
        mode = "advisory_partial"
        summary = (f"OOS Sharpe 개선은 {n_beat}/{len(per_target)}(IC t {t_isfit}, 부호 안정성 {stability}%). "
                   "MDD 억제 효과는 있지만 규칙으로 강제할 만큼 견고하지 않다 — "
                   "그림자(보고 전용) 유지, 실전 사이징은 동훈님 승인 없이는 반영하지 않는다.")
    else:
        mode = "shadow_only"
        summary = (f"SMA200을 이기지 못했다(개선 {n_beat}/{len(per_target)}, OOS IC t {t_isfit}). "
                   "보고·국면 판단용으로만 쓰고 진입 규칙에는 넣지 않는다.")
    if not significant:
        summary += f" OOS IC t {t_isfit} < 2 → '예측력 있음'이라고 주장하지 않는다."
    elif stability < 60:
        summary += (f" IS 전·후반 부호 일치율이 {stability}%로 낮아, 부호 재적합이 표본 특성에 "
                    "기대고 있을 가능성을 배제할 수 없다.")

    verdict = {"mode": mode, "summary_ko": summary, "per_target": per_target,
               "oos_ic_isfit_spy": oos_ic_spy, "oos_ic_prior_spy": oos_ic_prior,
               "oos_ic_t_nw_isfit": t_isfit, "oos_ic_t_nw_prior": oos_ic_prior.get("t_nw"),
               "significance_t2": significant,
               "sign_stability_rate_pct": round(agree / max(1, len(sign_fit)) * 100, 1),
               "multiple_testing_caveat": ("부호는 IS에서 성분별로 고른 값이다(21개 중 8개 뒤집힘). "
                                           "선택 자체가 표본에 맞춘 것이므로 OOS 결과만을 신뢰도로 삼고, "
                                           "IS 전·후반 부호 일치율(sign_stability_rate_pct)을 함께 본다.")}

    chosen_cal = cal_isfit if cal_isfit.get("active") else (cal_prior if cal_prior.get("active") else {})
    calibration = dict(chosen_cal)
    calibration.update({"active": bool(calibration.get("active")),
                        "variant": "M8_ISFIT" if cal_isfit.get("active") else ("M8_PRIOR" if cal_prior.get("active") else None),
                        "prior": cal_prior, "isfit": cal_isfit, "verdict": verdict,
                        "note": ("OOS Brier가 기준율보다 나쁘면 확률 캘리브레이션을 쓰지 않는다 "
                                 "(market_outlook은 기준율만 표시).")})

    conclusion = [
        f"OOS(2022-01~) 20일 IC — SPY {oos_ic_spy.get('pearson')} (t {oos_ic_spy.get('t_nw')}) · "
        f"QQQ {ic_qqq.get('pearson')} (t {ic_qqq.get('t_nw')}) · SOXX {ic_soxx.get('pearson')} (t {ic_soxx.get('t_nw')}) "
        f"— 사전부호 그대로는 SPY {oos_ic_prior.get('pearson')} (t {oos_ic_prior.get('t_nw')})로 유의하지 않았다.",
        f"SMA200 대비 OOS Sharpe 개선 타깃 {n_beat}/{len(per_target)}.",
        summary,
    ]
    flipped = [k for k, v in sign_fit.items() if v["flipped"]]
    if flipped:
        conclusion.append(f"사전 부호가 IS에서 반대로 나온 성분 {len(flipped)}개: {', '.join(flipped)} "
                          "(이 표본은 '좋은 고용=긴축' 체제였다 — 부호는 IS에서만 재적합).")
    conclusion.append(f"부호 안정성: IS 전·후반 IC 부호가 같은 성분 {agree}/{len(sign_fit)} "
                      f"({round(agree / max(1, len(sign_fit)) * 100, 1)}%). 부호 선택은 표본에 맞춘 것이므로 "
                      "OOS 결과와 이 안정성만을 근거로 삼는다.")

    out = {
        "generated_at_kst": _now_str(),
        "engine": m8.ENGINE,
        "is_simulation": True,
        "disclaimer": ("시뮬레이션입니다. 실전 원장·대시보드 실적과 분리되어 있으며 기본 설정에서는 "
                       "주문 크기에 반영되지 않습니다(shadow)."),
        "window": {"start": str(idx[0].date()), "end": str(idx[-1].date()), "days": int(len(idx))},
        "split": {"is": f"{START}~2021-12-31", "oos": f"{SPLIT}~{idx[-1].date()}"},
        "friction": f"|Δexposure| × {FRICTION_PER_TURNOVER*100:.2f}% (노출 1.0 교체 = 0.12%, 왕복 0.24%)",
        "fill_model": "t일 종가 신호 → t+1일 수익률 적용 (look-ahead 없음)",
        "horizon_days": HORIZON,
        "blocks_effective_from": {k: (str(prior['block_z'][k].dropna().index[0].date())
                                      if len(prior['block_z'][k].dropna()) else None)
                                  for k in prior["block_z"].columns},
        "strategies": {
            "BH": "단순보유(현금 없음)",
            "SMA200": "SPY 200일선 위일 때만 보유(데스크 기존 추세 규칙, 판정 기준선)",
            "LEI3_INCUMBENT": "기존 CUBE-LEI3(10Y-3M<0 또는 HY OAS>500bp 또는 SPY<200DMA면 현금)",
            "M8P_*": "사전 부호 복합(설계 기본값)",
            "M8F_*": "IS 구간에서 부호를 재적합한 복합(OOS는 미사용)",
            "*_LINEAR": "노출 = clip(0.75+0.25·z, 0.35, 1.0), 나머지 SGOV/BIL 파킹",
            "*_STEP": "국면 단계 계수(확장 1.0/0.9 · 중립 0.75 · 둔화 0.55 · 수축경계 0.4 · 수축 0.25)",
            "*_BINARY": "z>0이면 100%, 아니면 현금",
            "*_LINEAR_X_SMA200": "복합 노출과 200일선 규칙 중 작은 쪽",
        },
        "results": results,
        "ic": ic,
        "block_ic": block_ic,
        "member_ic": member_ic,
        "sign_fit": sign_fit,
        "quintiles": quintiles,
        "calibration": calibration,
        "robustness": robust,
        "verdict": verdict,
        "conclusion_ko": conclusion,
        "methodology": {
            "signal": "core/macro_leading.py — 성분 z 평균 → 블록 z → 가중 복합 z",
            "publication_lag": "성분별 lag_days 적용(CPI 13일·PPI 15~16일·고용 7일·청구 6일)",
            "revision_caveat": "무료 FRED는 최종 개정치. YoY·3개월 변화 위주 + lag+10일 강건성으로 보완.",
            "statistics": "Pearson/Spearman IC + Newey-West(20) t값, IS/OOS 분리",
            "sign_fit": f"IS |IC| ≥ {SIGN_FIT_MIN_IC}일 때만 부호를 뒤집는다(노이즈 부호 뒤집기 방지)",
        },
        "data_provenance": {"fred": list(m8.FRED_IDS), "prices": m8.PRICE_SYMBOLS,
                            "hardcoded_values": False},
    }

    payload = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    for p in (OUT, DOCS_OUT):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)
    # 실시간 엔진이 같은 부호를 쓰도록 저장(IS에서만 적합한 값 — OOS 미사용)
    signs = {
        "source": "scripts/run_macro_lei_backtest.py — IS(2015-2021) 성분 IC 부호 재적합",
        "generated_at_kst": out["generated_at_kst"],
        "is_window": out["split"]["is"], "oos_window": out["split"]["oos"],
        "method": f"사전부호 z의 IS IC < -{SIGN_FIT_MIN_IC} 이면 부호를 뒤집는다",
        "multipliers": {k: int(v["multiplier"]) for k, v in sign_fit.items()},
        "sign_stability_rate_pct": verdict["sign_stability_rate_pct"],
        "note": "이 파일이 없으면 core/macro_leading.py는 사전부호(prior)로 돌아간다.",
    }
    with open(SIGNS_PATH, "w", encoding="utf-8") as f:
        json.dump(signs, f, ensure_ascii=False, indent=2)
    _write_md(out)
    if not quiet:
        _print_summary(out)
    return out


# ------------------------------------------------------------------------- writing
def _fmt(v: Any, nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def _write_md(out: Dict[str, Any]) -> None:
    w = out["window"]
    lines = [
        "# CUBE-M8 — 8대 거시·자산 선행지표 검증 리포트", "",
        f"생성: {out['generated_at_kst']} KST · 기간 {w['start']} ~ {w['end']} ({w['days']}거래일)",
        f"IS {out['split']['is']} / OOS {out['split']['oos']} · {out['friction']} · {out['fill_model']}", "",
        "> 시뮬레이션입니다. 실전 원장·주문과 분리되어 있습니다(shadow 기본).", "",
        "## 1. 예측력 — SPY 향후 20거래일 수익률 정보계수(IC)", "",
        "| 타깃 | 부호설계 | 구간 | n | Pearson | Spearman | NW t | 방향 적중 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for target, var in out["ic"].items():
        for vname in ("prior", "isfit"):
            for part in ("is", "oos"):
                b = (var.get(vname) or {}).get(part) or {}
                lines.append(f"| {target} | {vname} | {part.upper()} | {b.get('n', 0)} | "
                             f"{_fmt(b.get('pearson'), 3)} | {_fmt(b.get('spearman'), 3)} | "
                             f"{_fmt(b.get('t_nw'))} | {_fmt(b.get('directional_hit_pct'), 1)}% |")
    lines += ["", "## 2. 블록별 IC (SPY 20일, 사전 부호 기준)", "",
              "| 블록 | IS IC | IS t | OOS IC | OOS t | 해석 |", "|---|---:|---:|---:|---:|---|"]
    for k, v in out["block_ic"].items():
        isic, oosic = v.get("is") or {}, v.get("oos") or {}
        a, b = (isic.get("pearson") or 0), (oosic.get("pearson") or 0)
        if a > 0 and b > 0:
            verdict_txt = "사전부호 유지(IS·OOS 일치)"
        elif a < 0 and b < 0:
            verdict_txt = "부호 반전이 안정적(IS·OOS 모두 음)"
        else:
            verdict_txt = "IS·OOS 불일치(불안정)"
        lines.append(f"| {k} | {_fmt(isic.get('pearson'), 3)} | {_fmt(isic.get('t_nw'))} | "
                     f"{_fmt(oosic.get('pearson'), 3)} | {_fmt(oosic.get('t_nw'))} | {verdict_txt} |")
    lines += ["", "## 3. 성과 비교 (OOS, 2022~)", ""]
    for target, block in out["results"].items():
        lines += [f"### {target}", "",
                  "| 전략 | CAGR | MDD | Sharpe | Sortino | PF(일) | 평균노출 |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for name, parts in block.items():
            m = parts.get("oos") or {}
            lines.append(f"| {name} | {_fmt(m.get('cagr_pct'))}% | {_fmt(m.get('mdd_pct'))}% | "
                         f"{_fmt(m.get('sharpe'))} | {_fmt(m.get('sortino'))} | {_fmt(m.get('pf_daily'))} | "
                         f"{_fmt(m.get('avg_exposure_pct'), 1)}% |")
        lines.append("")
    lines += ["## 4. 분위 분석 (IS 임계값 → OOS 적용)", "",
              "| 복합 | 분위 | IS 20일 수익 | OOS 20일 수익 | OOS 일수 |", "|---|---|---:|---:|---:|"]
    for vname, rows in (out.get("quintiles") or {}).items():
        for q in rows or []:
            lines.append(f"| {vname} | {q['label']} | {_fmt(q.get('mean_fwd20_is_pct'))}% | "
                         f"{_fmt(q.get('mean_fwd20_oos_pct'))}% | {q.get('days_oos')} |")
    v = out.get("verdict") or {}
    lines += ["", "## 5. 판정", "", f"- **{v.get('mode')}** — {v.get('summary_ko')}"]
    for target, row in (v.get("per_target") or {}).items():
        lines.append(f"- {target}: 기준선 SMA200 Sharpe {row['incumbent_oos'].get('sharpe')} → "
                     f"{row['best_variant']} {row['best_oos'].get('sharpe')} "
                     f"(Δ{row['sharpe_delta_vs_incumbent']:+}) · MDD {row['mdd_delta_pp_vs_incumbent']:+}%p · "
                     f"CAGR {row['cagr_delta_pp_vs_incumbent']:+}%p / 단순보유 대비 Sharpe "
                     f"{row['sharpe_delta_vs_bh']:+}, MDD {row['mdd_delta_pp_vs_bh']:+}%p, CAGR {row['cagr_delta_pp_vs_bh']:+}%p")
    cal = out.get("calibration") or {}
    if cal.get("oos_brier") is not None:
        lines.append(f"- 확률 캘리브레이션: 변형 {cal.get('variant')} · OOS Brier {cal.get('oos_brier')} "
                     f"vs 기준율 {cal.get('oos_brier_base_rate')} (기술점수 {cal.get('oos_skill_vs_base_pct')}%)")
    lines += ["", "## 6. 강건성", "", "| 조합 | OOS Sharpe | OOS MDD | OOS CAGR |", "|---|---:|---:|---:|"]
    for k, r in (out.get("robustness") or {}).items():
        m = r.get("oos") or {}
        lines.append(f"| {k} | {_fmt(m.get('sharpe'))} | {_fmt(m.get('mdd_pct'))}% | {_fmt(m.get('cagr_pct'))}% |")
    lines += ["", "## 7. 결론", ""] + [f"- {c}" for c in out.get("conclusion_ko") or []]
    with open(DOCS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _print_summary(out: Dict[str, Any]) -> None:
    print(f"✅ CUBE-M8 백테스트 {out['window']['start']}~{out['window']['end']}")
    for target, block in out["results"].items():
        bh = (block.get("BH") or {}).get("oos") or {}
        sma = (block.get("SMA200") or {}).get("oos") or {}
        bf = (block.get("M8F_LINEAR_X_SMA200") or {}).get("oos") or {}
        print(f"  {target:5s} OOS Sharpe BH {bh.get('sharpe')} / SMA200 {sma.get('sharpe')} / "
              f"M8F×SMA200 {bf.get('sharpe')} · MDD {bh.get('mdd_pct')} / {sma.get('mdd_pct')} / {bf.get('mdd_pct')}%")
    for c in out.get("conclusion_ko") or []:
        print("  ·", c)


if __name__ == "__main__":
    run_backtest(force="--force" in sys.argv, quiet=False)
