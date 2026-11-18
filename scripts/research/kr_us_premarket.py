"""한국 증시 ↔ 미국 프리장/정규장 선행성·수익성 백테스트 (2026-11-18 study).

질문
----
1) 한국 세션 D(15:30 KST 종료)의 지수·반도체·원화 움직임이 같은 날 미국 세션 D의
   프리장(04:00~09:30 ET = 18:00~23:30 KST, EST) 변동과 어떤 상관을 갖는가?
   그 정보는 04:00 ET(=18:00 KST) 시점에 이미 얼마나 가격에 반영되어 있는가?
2) 한국장 신호로 프리장에 진입(04:00 / 06:00 / 08:00 ET)해 프리장 종료(09:30 ET) 또는
   정규장 종가(16:00 ET)에 청산하는 전략이 왕복 0.24% + 프리장 추가 슬리피지를
   감안하고도 플러스 기대값을 갖는가? 프리장 진입이 정규장 시가 진입보다 나은가?

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
import re
from typing import Dict, List, Sequence

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
UNLEV = ["SOXX", "SMH", "QQQ"]
KR_SIGNALS = {
    "kospi": "kospi_ret",
    "kosdaq": "kosdaq_ret",
    "semis": "semis_ret",
    "semis_excess": "semis_excess",
    "usdkrw": "usdkrw_ret",
    "kospi_last_hour": "kospi_last_hour",
}
RESID_SIGNALS = {"kospi_resid": "kospi_ret", "semis_resid": "semis_ret"}

ENTRIES = {"0400": "pm_open_0400", "0600": "p_0600", "0800": "p_0800", "0930": "rth_open"}
EXITS = {"0930": "rth_open", "1600": "rth_close"}


# ------------------------------------------------------------------ helpers
def zexp(s: pd.Series, min_obs: int = 60) -> pd.Series:
    """expanding z-score (직전 표본 통계만 사용 — 미래정보 없음)."""
    m = s.expanding(min_periods=min_obs).mean().shift(1)
    sd = s.expanding(min_periods=min_obs).std().shift(1)
    return (s - m) / sd.replace(0, np.nan)


def ols_hc1(y: np.ndarray, X: np.ndarray) -> Dict:
    """OLS + HC1 robust SE. 인자 순서: y, 설계행렬 X(상수항 포함)."""
    XtX_inv = np.linalg.pinv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    n, k = X.shape
    S = X.T @ ((e ** 2).reshape(-1, 1) * X)
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


def trade_stats(rets: Sequence[float], dates: Sequence) -> Dict:
    r = np.asarray([x for x in rets if x is not None and np.isfinite(x)], dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0}
    wins, losses = r[r > 0], r[r <= 0]
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
    sig["composite_z"] = sig[["kospi_z", "semis_z", "semis_excess_z"]].mean(axis=1)
    return sig


def correlation_study(panel: pd.DataFrame, sig: pd.DataFrame) -> Dict:
    targets = {}
    for t in UNLEV + ["NVDA", "SOXL", "TQQQ", "SOXS"]:
        targets[f"{t}_pm_0400_0930"] = panel[f"{t}_pm_0400_0930"]
        targets[f"{t}_pm_0600_0930"] = panel[f"{t}_pm_0600_0930"]
        targets[f"{t}_gap_open"] = panel[f"{t}_gap"]
        targets[f"{t}_gap_prevclose_0400"] = panel[f"{t}_gap_prevclose_0400"]
        targets[f"{t}_rth_ret"] = panel[f"{t}_rth_ret"]
        targets[f"{t}_day_0400_1600"] = panel[f"{t}_day_0400_1600"]

    rows = []
    for sname in ["kospi", "kosdaq", "semis", "semis_excess", "usdkrw", "kospi_last_hour",
                  "kospi_resid", "semis_resid", "composite_z"]:
        x = sig[sname]
        for tname, y in targets.items():
            c = corr(x, y)
            if c.get("n", 0) < 10:
                continue
            rows.append({"signal": sname, "target": tname, **c})

    regressions = []
    for sname in ["kospi_z", "semis_z", "semis_excess_z", "composite_z", "semis_resid_z"]:
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


BETAS = {
    "gap_prevclose_0400": "gap_prevclose_0400",   # 전일 종가 → 04:00 프리장 시가
    "pm_0400_0930": "pm_0400_0930",               # 04:00 → 09:30 (프리장 창)
    "rth_0930_1600": "rth_ret",                   # 09:30 → 16:00 (정규장 창)
    "day_0400_1600": "day_0400_1600",             # 04:00 → 16:00 (프리장 진입 보유)
}


def window_betas(panel: pd.DataFrame, sig: pd.DataFrame) -> List[Dict]:
    """한국 신호 1σ당 각 창의 가격 반영(β, %)과 HC1 t값."""
    out = []
    for sname in ["kospi_z", "semis_z", "semis_excess_z", "composite_z", "semis_resid_z"]:
        for t in ["QQQ", "SOXX", "SMH", "NVDA", "SOXL", "TQQQ", "SOXS"]:
            row = {"signal": sname, "ticker": t}
            n = 0
            for label, col in BETAS.items():
                sub = pd.concat([sig[sname], panel[f"{t}_{col}"]], axis=1).dropna()
                if len(sub) < 60:
                    continue
                X = np.column_stack([np.ones(len(sub)), sub.iloc[:, 0].values])
                fit = ols_hc1(sub.iloc[:, 1].values, X)
                row[f"beta_{label}_pct"] = round(float(fit["coef"][0]) * 100, 3)
                row[f"t_{label}"] = round(float(fit["t"][0]), 2)
                n = len(sub)
            row["n"] = n
            out.append(row)
    return out


def bucket_table(panel: pd.DataFrame, sig: pd.DataFrame) -> List[Dict]:
    """신호 구간별 평균 수익률 — 선형 β로 드러나지 않는 꼬리(비선형) 효과 확인."""
    edges = [-99, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 99]
    labels = ["<-1.5", "-1.5~-1.0", "-1.0~-0.5", "-0.5~+0.5", "+0.5~+1.0", "+1.0~+1.5", ">+1.5"]
    out = []
    for sname in ["semis_z", "semis_resid_z", "composite_z"]:
        for t in ["SOXX", "SOXL", "SOXS"]:
            s = sig[sname]
            for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
                m = (s > lo) & (s <= hi)
                sub = panel.loc[m.fillna(False), [f"{t}_pm_0400_0930", f"{t}_rth_ret", f"{t}_day_0400_1600"]].dropna()
                if len(sub) < 10:
                    continue
                row = {"signal": sname, "ticker": t, "bucket": lab, "n": int(len(sub))}
                for col, key in [(f"{t}_pm_0400_0930", "pm"), (f"{t}_rth_ret", "rth"),
                                 (f"{t}_day_0400_1600", "day")]:
                    v = sub[col].values
                    row[f"{key}_mean_pct"] = round(float(v.mean()) * 100, 3)
                    row[f"{key}_t"] = round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2)
                out.append(row)
    return out


def divergence_table(panel: pd.DataFrame, sig: pd.DataFrame) -> List[Dict]:
    """한국 신호 부호 × 프리장 04:00 갭 부호 교차표 — 진입 시점에 알 수 있는 정보만."""
    out = []
    for t in ["NVDA", "SOXX", "SOXL", "TQQQ", "SOXS"]:
        for sname in ["semis_z", "composite_z", "semis_resid_z"]:
            sub = pd.concat([sig[sname], panel[f"{t}_gap_prevclose_0400"],
                             panel[f"{t}_pm_0400_0930"], panel[f"{t}_rth_ret"],
                             panel[f"{t}_day_0400_1600"]], axis=1).dropna()
            sub.columns = ["sig", "pm_gap", "pm", "rth", "day"]
            for s_sign, s_lab in [(1, "KR_up"), (-1, "KR_dn")]:
                for g_sign, g_lab in [(1, "PMgap_up"), (-1, "PMgap_dn")]:
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
def run_backtests(panel: pd.DataFrame, sig: pd.DataFrame, slip_pre: float,
                  tickers: Sequence[str] = tuple(US_TICKERS + UNLEV)) -> List[Dict]:
    """진입×청산×규칙 그리드. 모든 손익은 비용 차감 후 순수익률(%)."""
    results = []
    for t in tickers:
        for e_key, e_name in ENTRIES.items():
            for x_key, x_name in EXITS.items():
                if e_key == "0930" and x_key == "0930":
                    continue
                ent = panel[f"{t}_{e_name}"]
                ext = panel[f"{t}_{x_name}"]
                gross = ext / ent - 1.0
                slip_in = SLIP_REG if e_key == "0930" else slip_pre
                cost = 2 * COMMISSION_SIDE + slip_in + SLIP_REG
                rules = [("all", pd.Series(True, index=panel.index))]
                thrs = (1.0,) if e_key == "0930" else (1.0, 1.5)  # 정규장 진입 통제군은 1.0만
                for sname in ["kospi_z", "semis_z", "semis_excess_z", "composite_z",
                              "kospi_resid_z", "semis_resid_z", "usdkrw_z"]:
                    s = sig[sname]
                    for thr in thrs:
                        rules.append((f"{sname}_{thr:+.1f}", s > thr))
                        rules.append((f"{sname}_{-thr:+.1f}", s < -thr))
                for rname, mask in rules:
                    m = mask.fillna(False) & gross.notna()
                    if m.sum() < 20:
                        continue
                    net = gross[m] - cost
                    results.append({
                        "ticker": t, "entry_et": e_key, "exit_et": x_key, "rule": rname,
                        "cost_round_trip_pct": round(cost * 100, 2),
                        **trade_stats(net.values, list(panel.index[m])),
                    })
    return results


def robustness(panel: pd.DataFrame, sig: pd.Series, ticker: str, rule: str,
               entry: str, exit_: str, slip_pre: float = SLIP_PRE["base"]) -> Dict:
    """후보의 표본 분할·연도별·이상치 제거 안정성. 임계값은 규칙 문자열에서 파싱."""
    m = re.search(r"([+-]\d+\.\d+)$", rule)
    thr = float(m.group(1)) if m else 1.0
    gross = panel[f"{ticker}_{EXITS[exit_]}"] / panel[f"{ticker}_{ENTRIES[entry]}"] - 1.0
    mask = (sig > thr) if thr > 0 else (sig < thr)
    cost = 2 * COMMISSION_SIDE + (SLIP_REG if entry == "0930" else slip_pre) + SLIP_REG
    net = (gross - cost)[mask.fillna(False) & gross.notna()]
    if len(net) < 20:
        return {"n": 0}
    srt = np.sort(net.values)
    k = max(int(0.05 * len(srt)), 1)
    trimmed = srt[k:-k].mean() if len(srt) > 2 * k else np.nan
    half = len(net) // 2
    by_year = {str(y): round(float(v.mean()) * 100, 2) for y, v in net.groupby(net.index.year)}
    return {
        "n": int(len(net)),
        "ev_pct": round(float(net.mean()) * 100, 3),
        "trimmed5_pct": round(float(trimmed) * 100, 3),
        "median_pct": round(float(np.median(net)) * 100, 3),
        "first_half_pct": round(float(net.iloc[:half].mean()) * 100, 3),
        "second_half_pct": round(float(net.iloc[half:].mean()) * 100, 3),
        "by_year_pct": by_year,
    }


def long_short_combo(panel: pd.DataFrame, sig: pd.Series, thr: float,
                     long_t: str = "SOXL", inv_t: str = "SOXS",
                     entry: str = "0600", exit_: str = "0930", slip_pre: float = SLIP_PRE["base"]) -> Dict:
    """한국 강세 → SOXL 매수, 약세 → SOXS 매수 (현금성 계좌, 공매도 없음)."""
    slip_in = SLIP_REG if entry == "0930" else slip_pre
    cost = 2 * COMMISSION_SIDE + slip_in + SLIP_REG
    g_long = panel[f"{long_t}_{EXITS[exit_]}"] / panel[f"{long_t}_{ENTRIES[entry]}"] - 1.0
    g_inv = panel[f"{inv_t}_{EXITS[exit_]}"] / panel[f"{inv_t}_{ENTRIES[entry]}"] - 1.0
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
    """5년 일봉 교차확인. 원신호 + 전일 미국 세션 통제 잔차(resid) 모두."""
    d = pd.read_csv(DAILY, index_col=0, parse_dates=True)
    for label, col in [("kospi_resid", "kospi_ret"), ("semis_resid", "semis_ret")]:
        sub = d[[col, "SOXL_prev_ret"]].dropna()
        X = np.column_stack([np.ones(len(sub)), sub["SOXL_prev_ret"].values])
        b = np.linalg.pinv(X.T @ X) @ X.T @ sub[col].values
        r = pd.Series(np.nan, index=d.index)
        r.loc[sub.index] = sub[col].values - X @ b
        d[label] = r
    rows = []
    for sname, scol in [("kospi", "kospi_ret"), ("kosdaq", "kosdaq_ret"),
                        ("semis", "semis_ret"), ("semis_excess", "semis_excess"),
                        ("kospi_resid", "kospi_resid"), ("semis_resid", "semis_resid")]:
        for t in US_TICKERS + ["SOXX"]:
            for tgt in [f"{t}_gap", f"{t}_o2c", f"{t}_daily_ret"]:
                if tgt not in d:
                    continue
                c = corr(d[scol], d[tgt])
                if c.get("n", 0) < 10:
                    continue
                row = {"signal": sname, "target": tgt, **c}
                j = d[[scol, tgt]].dropna()
                X = np.column_stack([np.ones(len(j)), j[scol].values])
                fit = ols_hc1(j[tgt].values, X)
                row["beta_pct_per_1pct_signal"] = round(float(fit["coef"][0]) * 100, 3)
                row["t_beta"] = round(float(fit["t"][0]), 2)
                rows.append(row)
    return rows


def daily_tail() -> List[Dict]:
    """5년 일봉에서 신호 꼬리(하위 5%/10%, 상위 10%) 구간의 갭·장중 평균 — 표본 외 확인."""
    d = pd.read_csv(DAILY, index_col=0, parse_dates=True)
    sub = d[["semis_ret", "SOXL_prev_ret"]].dropna()
    X = np.column_stack([np.ones(len(sub)), sub["SOXL_prev_ret"].values])
    b = np.linalg.pinv(X.T @ X) @ X.T @ sub["semis_ret"].values
    d["semis_resid"] = np.nan
    d.loc[sub.index, "semis_resid"] = sub["semis_ret"].values - X @ b
    s = d["semis_resid"].dropna()
    q05, q10, q90 = s.quantile(0.05), s.quantile(0.10), s.quantile(0.90)
    buckets = {
        f"bottom5(<{q05*100:.2f}%)": d["semis_resid"] <= q05,
        f"bottom10(<{q10*100:.2f}%)": d["semis_resid"] <= q10,
        f"top10(>{q90*100:.2f}%)": d["semis_resid"] >= q90,
        "middle": (d["semis_resid"] > q10) & (d["semis_resid"] < q90),
        "all": d["semis_resid"].notna(),
    }
    out = []
    for t in ["SOXX", "SOXL", "NVDA"]:
        for lab, mask in buckets.items():
            g = d.loc[mask & d[f"{t}_o2c"].notna(), [f"{t}_gap", f"{t}_o2c"]]
            if len(g) < 20:
                continue
            row = {"ticker": t, "bucket": lab, "n": int(len(g))}
            for col, key in [(f"{t}_gap", "gap"), (f"{t}_o2c", "o2c")]:
                v = g[col].values
                row[f"{key}_mean_pct"] = round(float(v.mean()) * 100, 3)
                row[f"{key}_t"] = round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2)
            out.append(row)
    return out


def main() -> Dict:
    panel = pd.read_csv(PANEL, index_col=0, parse_dates=True)
    sig = build_signals(panel)
    corr_study = correlation_study(panel, sig)
    div = divergence_table(panel, sig)
    bt = run_backtests(panel, sig, SLIP_PRE["base"])
    bt_tight = run_backtests(panel, sig, SLIP_PRE["tight"], tickers=US_TICKERS)
    bt_wide = run_backtests(panel, sig, SLIP_PRE["wide"], tickers=US_TICKERS)
    wb = window_betas(panel, sig)

    combos = []
    for sname in ["composite_z", "semis_z", "kospi_z", "semis_resid_z"]:
        for entry in ("0400", "0600", "0930"):
            for exit_ in ("0930", "1600"):
                if entry == "0930" and exit_ == "0930":
                    continue
                combos.append({"signal": sname,
                               **long_short_combo(panel, sig[sname], 1.0, entry=entry, exit_=exit_)})

    rob = []
    for b in bt:
        if b["ticker"] in US_TICKERS and b.get("n", 0) >= 50 and b["rule"] != "all" \
                and abs(b.get("t_stat") or 0) >= 1.9:
            rob.append({**{k: b[k] for k in ("ticker", "entry_et", "exit_et", "rule", "ev_pct", "t_stat")},
                        **robustness(panel, sig[b["rule"].rsplit("_", 1)[0]], b["ticker"],
                                     b["rule"], b["entry_et"], b["exit_et"])})

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
            "round_trip_cost_pct_regular_only": round((2 * COMMISSION_SIDE + 2 * SLIP_REG) * 100, 2),
        },
        "correlations": corr_study["correlations"],
        "regressions": corr_study["regressions"],
        "window_betas": wb,
        "bucket_table": bucket_table(panel, sig),
        "divergence": div,
        "backtests_base": bt,
        "backtests_tight_slippage": bt_tight,
        "backtests_wide_slippage": bt_wide,
        "long_short_combos": combos,
        "robustness": rob,
        "daily_5y_check": daily_check(),
        "daily_5y_tail": daily_tail(),
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[saved] {OUT_JSON}  backtests={len(bt)} combos={len(combos)} "
          f"robustness={len(rob)} window_betas={len(wb)}")
    return out


def _print(res: Dict) -> None:
    print("\n== 창별 β (한국 신호 1σ → 창 수익률 %) ==")
    print("%-14s %-5s %16s %16s %16s" % ("signal", "tkr", "gap→04:00", "04:00→09:30", "09:30→16:00"))
    for w in res["window_betas"]:
        if w["signal"] in ("semis_z", "composite_z", "semis_resid_z") and w["ticker"] in ("SOXX", "SOXL", "NVDA", "SOXS", "QQQ"):
            print("%-14s %-5s %+7.3f%% t=%5.2f %+7.3f%% t=%5.2f %+7.3f%% t=%5.2f" % (
                w["signal"], w["ticker"],
                w.get("beta_gap_prevclose_0400_pct", 0), w.get("t_gap_prevclose_0400", 0),
                w.get("beta_pm_0400_0930_pct", 0), w.get("t_pm_0400_0930", 0),
                w.get("beta_rth_0930_1600_pct", 0), w.get("t_rth_0930_1600", 0)))
    print("\n== 상위 순수익(n>=50, t순) ==")
    rows = [b for b in res["backtests_base"] if b.get("n", 0) >= 50 and b["rule"] != "all"]
    for b in sorted(rows, key=lambda z: -(z["t_stat"] or -9))[:14]:
        print(f"{b['ticker']:<5} {b['entry_et']}->{b['exit_et']} {b['rule']:<20} n={b['n']:>3} "
              f"ev={b['ev_pct']:>6}% win={b['win_pct']:>5}% pf={b['pf']} t={b['t_stat']} mdd={b['mdd_pct']}%")
    print("\n== 프리장 창(04:00/06:00/08:00 → 09:30) 최고/최저 ==")
    pm = [b for b in res["backtests_base"] if b["exit_et"] == "0930" and b["ticker"] in US_TICKERS
          and b.get("n", 0) >= 30]
    for b in sorted(pm, key=lambda z: -z["ev_pct"])[:5]:
        print(f"{b['ticker']:<5} {b['entry_et']}->0930 {b['rule']:<20} n={b['n']:>3} ev={b['ev_pct']}% t={b['t_stat']}")
    print("  --- 최저 ---")
    for b in sorted(pm, key=lambda z: z["ev_pct"])[:3]:
        print(f"{b['ticker']:<5} {b['entry_et']}->0930 {b['rule']:<20} n={b['n']:>3} ev={b['ev_pct']}% t={b['t_stat']}")
    print("\n== 안정성(t>=1.9 상위 6) ==")
    for r in sorted(res["robustness"], key=lambda z: -(z.get("t_stat") or -9))[:6]:
        print(f"{r['ticker']:<5} {r['entry_et']}->{r['exit_et']} {r['rule']:<20} n={r['n']:>3} ev={r['ev_pct']}% "
              f"trim5={r['trimmed5_pct']}% 1H={r['first_half_pct']}% 2H={r['second_half_pct']}% by_year={r['by_year_pct']}")
    print("\n== 롱/인버스 콤보 ==")
    for c in res["long_short_combos"]:
        if c.get("n"):
            print(f"{c['signal']:<14} thr={c['thr']} {c['entry_et']}->{c['exit_et']} n={c['n']:>3} "
                  f"ev={c['ev_pct']}% win={c['win_pct']}% pf={c['pf']} t={c['t_stat']} mdd={c['mdd_pct']}%")


if __name__ == "__main__":
    _print(main())
