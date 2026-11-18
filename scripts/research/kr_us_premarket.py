"""한국 증시 ↔ 미국 프리장/정규장 선행성·수익성 백테스트 (2026-11-18 study).

질문
----
1) 한국 세션 D(15:30 KST 종료)의 지수·반도체·원화 움직임이 같은 날 미국 세션 D의
   프리장(04:00~09:30 ET = 18:00~23:30 KST, EST) 변동과 어떤 상관을 갖는가?
2) 한국장 신호로 프리장에 진입(04:00 / 06:00 / 08:00 ET)해 프리장 종료(09:30 ET) 또는
   정규장 종가(16:00 ET)에 청산하는 전략이 왕복 0.24% + 프리장 추가 슬리피지를
   감안하고도 플러스 기대값을 갖는가?

누수 차단
---------
신호는 D의 한국 세션 종가(15:30 KST = 01:30 ET)로, D의 미국 프리장 시작(04:00 ET)보다
2.5시간 앞선다. z-score는 expanding(직전 표본만)으로 계산해 미래 통계를 쓰지 않는다.

비용
----
수수료 0.12%/side(왕복 0.24%) + 슬리피지: 프리장 0.15%/side, 정규장 0.05%/side.
프리장 슬리피지 0.05%/0.30%로 민감도도 함께 산출한다.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats as st

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PANEL = os.path.join(BASE_DIR, "data_cache", "research", "kr_us", "panel_60m.csv")
DAILY = os.path.join(BASE_DIR, "data_cache", "research", "kr_us", "panel_daily_5y.csv")
OUT_JSON = os.path.join(BASE_DIR, "docs", "kr_us_premarket_research.json")

COMMISSION_SIDE = 0.0012          # 왕복 0.24%
SLIP_PRE = {"base": 0.0015, "tight": 0.0005, "wide": 0.0030}
SLIP_REG = 0.0005                 # 정규장 시가/종가(유동성 높음)

US_TICKERS = ["NVDA", "SOXL", "TQQQ", "SOXS", "SQQQ"]
KR_SIGNALS = {
    "kospi": "kospi_ret",
    "kosdaq": "kosdaq_ret",
    "semis": "semis_ret",
    "semis_excess": "semis_excess",
    "usdkrw": "usdkrw_ret",
    "kospi_last_hour": "kospi_last_hour",
}
RESID_SIGNALS = {"kospi_resid": "kospi_ret", "semis_resid": "semis_ret"}


# ------------------------------------------------------------------ helpers
def zexp(s: pd.Series, min_obs: int = 60) -> pd.Series:
    """expanding z-score (직전 표본 통계만 사용 — 미래정보 없음)."""
    m = s.expanding(min_periods=min_obs).mean().shift(1)
    sd = s.expanding(min_periods=min_obs).std().shift(1)
    return (s - m) / sd.replace(0, np.nan)


def ols_hc1(y: np.ndarray, X: np.ndarray) -> Dict:
    """OLS + HC1 robust SE. X는 상수항 포함."""
    XtX_inv = np.linalg.pinv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    n, k = X.shape
    meal = (e ** 2).reshape(-1, 1) * X
    S = X.T @ meal
    cov = XtX_inv @ S @ XtX_inv * (n / (n - k))
    se = np.sqrt(np.diag(cov))
    t = b / se
    p = 2 * (1 - st.t.cdf(np.abs(t), df=n - k))
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (e ** 2).sum() / ss_tot if ss_tot else np.nan
    return {"coef": b, "se": se, "t": t, "p": p, "r2": float(r2), "n": int(n)}


def corr(x: pd.Series, y: pd.Series) -> Dict:
    j = pd.concat([x, y], axis=1).dropna()
    if len(j) < 10:
        return {"n": int(len(j))}
    r, p = st.pearsonr(j.iloc[:, 0], j.iloc[:, 1])
    rho, _ = st.spearmanr(j.iloc[:, 0], j.iloc[:, 1])
    return {"n": int(len(j)), "r": round(float(r), 3), "p": float(f"{p:.2e}"),
            "rho": round(float(rho), 3)}


def trade_stats(rets: Sequence[float], dates: Sequence, win_floor: float = 0.0) -> Dict:
    r = np.asarray([x for x in rets if x is not None and np.isfinite(x)], dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0}
    wins, losses = r[r > win_floor], r[r <= win_floor]
    gw, gl = float(wins.sum()), float(abs(losses.sum()))
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min())
    t = float(r.mean() / (r.std(ddof=1) / np.sqrt(n))) if n > 2 and r.std(ddof=1) > 0 else None
    yrs = max((max(dates) - min(dates)).days / 365.25, 1e-9)
    return {
        "n": n,
        "win_pct": round(100 * len(wins) / n, 1),
        "ev_pct": round(float(r.mean()) * 100, 3),
        "median_pct": round(float(np.median(r)) * 100, 3),
        "sd_pct": round(float(r.std(ddof=1)) * 100, 2),
        "pf": round(gw / gl, 2) if gl > 0 else None,
        "t_stat": round(t, 2) if t is not None else None,
        "total_comp_pct": round(float(eq[-1] - 1) * 100, 1),
        "mdd_pct": round(mdd * 100, 1),
        "trades_per_year": round(n / yrs, 1),
        "best_pct": round(float(r.max()) * 100, 2),
        "worst_pct": round(float(r.min()) * 100, 2),
        "avg_win_pct": round(float(wins.mean()) * 100, 2) if len(wins) else 0.0,
        "avg_loss_pct": round(float(losses.mean()) * 100, 2) if len(losses) else 0.0,
    }


# ------------------------------------------------------------------ signals
def build_signals(panel: pd.DataFrame) -> pd.DataFrame:
    sig = pd.DataFrame(index=panel.index)
    for name, col in KR_SIGNALS.items():
        sig[name] = panel[col]
        sig[f"{name}_z"] = zexp(panel[col])
    # 전일 미국 세션(그 밤)을 통제한 한국 고유 움직임
    ctl_cols = ["us_prev_SOXL_day", "us_prev_SOXL_ah"]
    for label, col in RESID_SIGNALS.items():
        sub = panel[[col] + ctl_cols].dropna()
        y = sub[col].values
        X = np.column_stack([np.ones(len(sub))] + [sub[c].values for c in ctl_cols])
        b = np.linalg.pinv(X.T @ X) @ X.T @ y
        resid = pd.Series(np.nan, index=panel.index)
        resid.loc[sub.index] = y - X @ b
        sig[label] = resid
        sig[f"{label}_z"] = zexp(resid)
    # 복합 점수: kospi_z, semis_z, semis_excess_z 평균
    sig["composite_z"] = sig[["kospi_z", "semis_z", "semis_excess_z"]].mean(axis=1)
    return sig


def correlation_study(panel: pd.DataFrame, sig: pd.DataFrame) -> Dict:
    targets = {}
    for t in US_TICKERS:
        targets[f"{t}_pm_0400_0930"] = panel[f"{t}_pm_0400_0930"]
        targets[f"{t}_pm_0600_0930"] = panel[f"{t}_pm_0600_0930"]
        targets[f"{t}_gap_open"] = panel[f"{t}_gap"]          # 전일 종가 → 정규장 시가
        targets[f"{t}_gap_prevclose_0400"] = panel[f"{t}_gap_prevclose_0400"]
        targets[f"{t}_rth_ret"] = panel[f"{t}_rth_ret"]
        targets[f"{t}_day_0400_1600"] = panel[f"{t}_day_0400_1600"]

    rows = []
    for sname in ["kospi", "kosdaq", "semis", "semis_excess", "usdkrw", "kospi_last_hour",
                  "kospi_resid", "semis_resid", "composite_z"]:
        x = sig[sname] if sname.endswith("_z") or sname.endswith("_resid") else sig[sname]
        for tname, y in targets.items():
            c = corr(x, y)
            if c.get("n", 0) < 10:
                continue
            rows.append({"signal": sname, "target": tname, **c})

    # 미국 전일 세션을 통제한 회귀: PM 수익률 ~ 한국신호 + 전일 미국 세션
    regressions = []
    for sname in ["kospi_z", "semis_z", "semis_excess_z", "composite_z"]:
        for tname in ["NVDA_pm_0400_0930", "SOXL_pm_0400_0930", "TQQQ_pm_0400_0930"]:
            sub = pd.concat([sig[sname], panel[tname], panel["us_prev_SOXL_day"]], axis=1).dropna()
            if len(sub) < 60:
                continue
            y = sub.iloc[:, 1].values
            X = np.column_stack([np.ones(len(sub)), sub.iloc[:, 0].values, sub.iloc[:, 2].values])
            fit = ols_hc1(y, X)
            regressions.append({
                "spec": f"{tname} ~ {sname} + us_prev_SOXL_day",
                "n": fit["n"], "r2": round(fit["r2"], 3),
                "beta_signal": round(float(fit["coef"][1]), 5),
                "t_signal": round(float(fit["t"][1]), 2),
                "p_signal": float(f"{fit['p'][1]:.2e}"),
                "beta_prev_us": round(float(fit["coef"][2]), 4),
                "t_prev_us": round(float(fit["t"][2]), 2),
            })
    return {"correlations": rows, "regressions": regressions}


def divergence_table(panel: pd.DataFrame, sig: pd.DataFrame) -> List[Dict]:
    """한국 신호 부호 × 프리장 04:00 갭 부호 교차표 — 프리장 진입 시점에 알 수 있는 정보만."""
    out = []
    for t in ["NVDA", "SOXL", "TQQQ"]:
        for sname in ["semis_z", "composite_z"]:
            sub = pd.concat([sig[sname], panel[f"{t}_gap_prevclose_0400"],
                             panel[f"{t}_pm_0400_0930"], panel[f"{t}_rth_ret"],
                             panel[f"{t}_day_0400_1600"]], axis=1).dropna()
            sub.columns = ["sig", "pm_gap", "pm", "rth", "day"]
            for s_sign, s_lab in [(1, "KR_up"), (-1, "KR_dn")]:
                for g_sign, g_lab in [(1, "PM_gap_up"), (-1, "PM_gap_dn")]:
                    m = (np.sign(sub["sig"]) == s_sign) & (np.sign(sub["pm_gap"]) == g_sign)
                    g = sub[m]
                    if len(g) < 10:
                        continue
                    row = {"ticker": t, "signal": sname, "bucket": f"{s_lab}·{g_lab}", "n": int(len(g))}
                    for col in ["pm", "rth", "day"]:
                        v = g[col].values
                        tt = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
                        row[f"{col}_mean_pct"] = round(float(v.mean()) * 100, 3)
                        row[f"{col}_t"] = round(float(tt), 2)
                    out.append(row)
    return out


# ---------------------------------------------------------------- backtests
ENTRIES = {"0400": "pm_open_0400", "0600": "p_0600", "0800": "p_0800"}
EXITS = {"0930": "rth_open", "1600": "rth_close"}


def run_backtests(panel: pd.DataFrame, sig: pd.DataFrame, slip_pre: float = SLIP_PRE["base"]) -> List[Dict]:
    results = []
    entry_cost = 2 * COMMISSION_SIDE + slip_pre + SLIP_REG  # 진입(프리장) + 청산(정규장)
    for t in US_TICKERS:
        for e_key, e_col in ENTRIES.items():
            for x_key, x_col in EXITS.items():
                if x_key == "0930" and e_key == "0800":
                    pass  # 30분 보유도 유효한 비교군
                ent = panel[f"{t}_{e_col}"]
                ext = panel[f"{t}_{x_col}"]
                gross = ext / ent - 1.0
                rules = [("all", pd.Series(True, index=panel.index))]
                for sname in ["kospi_z", "semis_z", "semis_excess_z", "composite_z",
                              "kospi_resid_z", "semis_resid_z"]:
                    s = sig[sname]
                    if t in ("SOXS", "SQQQ"):
                        rules += [(f"{sname}<-0.5", s < -0.5), (f"{sname}<-1.0", s < -1.0),
                                  (f"{sname}<-1.5", s < -1.5)]
                    else:
                        rules += [(f"{sname}>0.5", s > 0.5), (f"{sname}>1.0", s > 1.0),
                                  (f"{sname}>1.5", s > 1.5)]
                for rname, mask in rules:
                    m = mask.fillna(False) & gross.notna()
                    if m.sum() < 20:
                        continue
                    net = gross[m] - entry_cost
                    results.append({
                        "ticker": t, "entry_et": e_key, "exit_et": x_key, "rule": rname,
                        "cost_round_trip_pct": round(entry_cost * 100, 2),
                        **trade_stats(net.values, list(panel.index[m])),
                    })
    return results


def long_short_combo(panel: pd.DataFrame, sig: pd.Series, thr: float,
                     long_t: str = "SOXL", inv_t: str = "SOXS",
                     entry: str = "0600", exit_: str = "0930", slip_pre: float = SLIP_PRE["base"]) -> Dict:
    """한국 강세 → SOXL 매수, 약세 → SOXS 매수 (현금성 계좌, 공매도 없음)."""
    cost = 2 * COMMISSION_SIDE + slip_pre + SLIP_REG
    e_col = ENTRIES[entry]
    x_col = EXITS[exit_]
    g_long = panel[f"{long_t}_{x_col}"] / panel[f"{long_t}_{e_col}"] - 1.0
    g_inv = panel[f"{inv_t}_{x_col}"] / panel[f"{inv_t}_{e_col}"] - 1.0
    rets, dates, sides = [], [], []
    for d in panel.index:
        z = sig.get(d, np.nan)
        if not np.isfinite(z):
            continue
        if z > thr and np.isfinite(g_long.get(d, np.nan)):
            rets.append(g_long[d] - cost); dates.append(d); sides.append(long_t)
        elif z < -thr and np.isfinite(g_inv.get(d, np.nan)):
            rets.append(g_inv[d] - cost); dates.append(d); sides.append(inv_t)
    st_ = trade_stats(rets, dates)
    st_.update({"thr": thr, "entry_et": entry, "exit_et": exit_, "pair": f"{long_t}/{inv_t}",
                "long_trades": sides.count(long_t), "inverse_trades": sides.count(inv_t)})
    return st_


# -------------------------------------------------------------- 5y daily check
def daily_check() -> List[Dict]:
    d = pd.read_csv(DAILY, index_col=0, parse_dates=True)
    rows = []
    for sname, scol in [("kospi", "kospi_ret"), ("kosdaq", "kosdaq_ret"),
                        ("semis", "semis_ret"), ("semis_excess", "semis_excess")]:
        for t in US_TICKERS:
            for tgt in [f"{t}_gap", f"{t}_o2c", f"{t}_daily_ret"]:
                if tgt not in d:
                    continue
                c = corr(d[scol], d[tgt])
                if c.get("n", 0) < 10:
                    continue
                rows.append({"signal": sname, "target": tgt, **c})
    return rows


def main() -> Dict:
    panel = pd.read_csv(PANEL, index_col=0, parse_dates=True)
    sig = build_signals(panel)
    corr_study = correlation_study(panel, sig)
    div = divergence_table(panel, sig)
    bt = run_backtests(panel, sig, SLIP_PRE["base"])
    bt_tight = run_backtests(panel, sig, SLIP_PRE["tight"])
    bt_wide = run_backtests(panel, sig, SLIP_PRE["wide"])

    combos = []
    for sname in ["composite_z", "semis_z", "kospi_z", "semis_resid_z"]:
        for thr in (0.5, 1.0):
            for entry in ("0400", "0600", "0800"):
                for exit_ in ("0930", "1600"):
                    combos.append({"signal": sname, **long_short_combo(panel, sig[sname], thr,
                                                                        entry=entry, exit_=exit_)})

    # 프리장 진입 비용 없이(참고): 신호의 총 예측력 확인용 그로스
    gross_only = []
    for sname in ["kospi_z", "semis_z", "composite_z", "semis_resid_z", "kospi_resid_z"]:
        for t in ["NVDA", "SOXL", "TQQQ"]:
            for entry in ("0400", "0600"):
                g = panel[f"{t}_rth_open"] / panel[f"{t}_{ENTRIES[entry]}"] - 1.0
                m = (sig[sname] > 1.0).fillna(False) & g.notna()
                if m.sum() < 20:
                    continue
                v = g[m].values
                gross_only.append({"signal": sname, "ticker": t, "entry_et": entry,
                                   "rule": f"{sname}>1.0", "n": int(m.sum()),
                                   "gross_ev_pct": round(float(v.mean()) * 100, 3),
                                   "gross_t": round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2)})

    out = {
        "meta": {
            "as_of": "2026-11-18",
            "panel": {"sessions": int(len(panel)),
                      "range": [str(panel.index.min().date()), str(panel.index.max().date())]},
            "commission_side_pct": COMMISSION_SIDE * 100,
            "slip_premarket_side_pct": {k: v * 100 for k, v in SLIP_PRE.items()},
            "slip_regular_side_pct": SLIP_REG * 100,
            "round_trip_cost_pct_base": round((2 * COMMISSION_SIDE + SLIP_PRE["base"] + SLIP_REG) * 100, 2),
            "round_trip_cost_pct_tight": round((2 * COMMISSION_SIDE + SLIP_PRE["tight"] + SLIP_REG) * 100, 2),
            "round_trip_cost_pct_wide": round((2 * COMMISSION_SIDE + SLIP_PRE["wide"] + SLIP_REG) * 100, 2),
        },
        "correlations": corr_study["correlations"],
        "regressions": corr_study["regressions"],
        "divergence": div,
        "backtests_base": bt,
        "backtests_tight_slippage": bt_tight,
        "backtests_wide_slippage": bt_wide,
        "long_short_combos": combos,
        "gross_only": gross_only,
        "daily_5y_check": daily_check(),
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[saved] {OUT_JSON}  backtests={len(bt)} combos={len(combos)}")
    return out


if __name__ == "__main__":
    res = main()
    # 콘솔 요약
    key_targets = [f"{t}_pm_0400_0930" for t in ("NVDA", "SOXL", "TQQQ")] + \
                  [f"{t}_rth_ret" for t in ("NVDA", "SOXL", "TQQQ")] + \
                  ["SOXL_gap_open", "SOXL_gap_prevclose_0400"]
    print("\n== 상관 (신호 × 타깃) ==")
    for r in res["correlations"]:
        if r["target"] in key_targets and r["signal"] in ("kospi", "semis", "semis_excess", "usdkrw",
                                                          "kospi_resid", "semis_resid", "composite_z"):
            print(f"{r['signal']:<14} {r['target']:<26} r={r['r']:>7} rho={r['rho']:>7} n={r['n']:>4} p={r['p']:.1e}")
    print("\n== 상위 백테스트(기대값 순, n>=60) ==")
    rows = [b for b in res["backtests_base"] if b.get("n", 0) >= 60]
    for b in sorted(rows, key=lambda z: -z["ev_pct"])[:18]:
        print(f"{b['ticker']:<5} {b['entry_et']}->{b['exit_et']} {b['rule']:<18} n={b['n']:>3} "
              f"ev={b['ev_pct']:>6}% win={b['win_pct']:>5}% pf={b['pf']} t={b['t_stat']} mdd={b['mdd_pct']}%")
    print("\n== 하위 ==")
    for b in sorted(rows, key=lambda z: z["ev_pct"])[:8]:
        print(f"{b['ticker']:<5} {b['entry_et']}->{b['exit_et']} {b['rule']:<18} n={b['n']:>3} ev={b['ev_pct']}% t={b['t_stat']}")
    print("\n== 롱/인버스 콤보 ==")
    for c in res["long_short_combos"]:
        if c.get("n"):
            print(f"{c['signal']:<14} thr={c['thr']} {c['entry_et']}->{c['exit_et']} n={c['n']:>3} "
                  f"ev={c['ev_pct']}% win={c['win_pct']}% pf={c['pf']} t={c['t_stat']} mdd={c['mdd_pct']}%")
