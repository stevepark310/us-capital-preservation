#!/usr/bin/env python3
"""
오늘 밤(2026-11-20 23:30 KST 개장) SQQQ 1주 매도 선택지의 실측 근거.

    python3 scripts/research/run_tonight_sqqq.py

계산하는 것
-----------
1. **한국장 급락 다음 미국 개장 갭** — KOSPI ≤ -3%인 날, 같은 ET 날짜 미국 세션의
   QQQ·SQQQ 갭(시가/전일종가-1)과 장중 경로(시가→종가). 5년 일봉.
2. **SQQQ 전일 +5% 이상 상승 다음 날** — 갭·장중 경로 분포. 오늘의 형태(11-19 +6.6%).
3. **60세션 10분봉에서 SQQQ가 세션 종가 +5% 이상인 날의 다음 세션 경로** —
   개장 갭, 10:00 가격, 종가, 그리고 49.42 기준 목표가(+2% = 50.40) 도달 여부.
4. 선택지별 기대 손익(1주·3주).

규약: 갭은 Open(D)/Close(D-1)-1, 장중은 Close(D)/Open(D)-1. 수수료 왕복 0.24%.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

import pandas as pd  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
RESEARCH = os.path.join(BASE_DIR, "data_cache", "research")
OUT = os.path.join(DATA, "research_tonight_sqqq_20261120.json")
KST = timedelta(hours=9)
RT = 0.0024
ENTRY = 48.18
LAST_YAHOO = 49.42
LAST_NH = 49.68


def now_kst():
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


def daily(sym: str, refresh: bool = True) -> pd.DataFrame:
    """일봉 — 캐시 우선, refresh면 yfinance로 5년 재수집(지수는 캐시만)."""
    path = os.path.join(RESEARCH, sym if sym.endswith(".pkl") else f"{sym}_1d_5y.pkl")
    df = None
    if os.path.exists(path):
        try:
            obj = pd.read_pickle(path)
            df = obj.get("frames", {}).get(sym) if isinstance(obj, dict) and "frames" in obj else obj
            if not isinstance(df, pd.DataFrame) or not len(df):
                df = None
        except Exception:
            df = None
    if refresh and sym in ("SQQQ", "QQQ", "SOXL", "^VIX"):
        try:
            import yfinance as yf
            fresh = yf.download(sym, period="5y", interval="1d", progress=False, auto_adjust=False)
            if hasattr(fresh.columns, "nlevels") and fresh.columns.nlevels > 1:
                fresh.columns = [c[0] for c in fresh.columns]
            fresh.index = pd.to_datetime([d.date() for d in fresh.index])
            if len(fresh):
                df = fresh[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            pass
    return df if df is not None else pd.DataFrame()


def describe(vals):
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)
    return {"n": n, "mean": round(statistics.mean(s), 2), "median": round(statistics.median(s), 2),
            "p25": round(s[int(n * 0.25)], 2), "p75": round(s[int(n * 0.75)], 2),
            "min": round(s[0], 2), "max": round(s[-1], 2),
            "pos_pct": round(sum(1 for x in s if x > 0) / n * 100, 1)}


def gap_events(qqq, sqqq, ks, kospi_thr=-3.0):
    """KOSPI ≤ thr 인 날 → 같은 날짜 미국 세션 갭·장중."""
    rows = []
    kret = ks["Close"].pct_change() * 100
    for day, ret in kret.items():
        if ret is None or ret > kospi_thr:
            continue
        if day not in qqq.index or day not in sqqq.index:
            continue
        prev_q = qqq.index.get_indexer([day])[0]
        if prev_q == 0:
            continue
        d0q = qqq.index[prev_q - 1]
        d0s = sqqq.index[sqqq.index.get_indexer([day])[0] - 1]
        q = {"date": str(day.date()), "kospi_ret": round(float(ret), 2),
             "qqq_gap": round((qqq.loc[day, "Open"] / qqq.loc[d0q, "Close"] - 1) * 100, 2),
             "qqq_intraday": round((qqq.loc[day, "Close"] / qqq.loc[day, "Open"] - 1) * 100, 2),
             "sqqq_gap": round((sqqq.loc[day, "Open"] / sqqq.loc[d0s, "Close"] - 1) * 100, 2),
             "sqqq_intraday": round((sqqq.loc[day, "Close"] / sqqq.loc[day, "Open"] - 1) * 100, 2),
             "sqqq_open_to_high": round((sqqq.loc[day, "High"] / sqqq.loc[day, "Open"] - 1) * 100, 2),
             "sqqq_open_to_low": round((sqqq.loc[day, "Low"] / sqqq.loc[day, "Open"] - 1) * 100, 2)}
        rows.append(q)
    return rows


def after_big_up(df, thr=5.0):
    """전일 +thr% 이상 상승한 날의 '다음 날' 갭·장중."""
    ret = df["Close"].pct_change() * 100
    rows = []
    idx = list(df.index)
    for i in range(1, len(idx) - 1):
        if ret.iloc[i] is None or ret.iloc[i] < thr or ret.iloc[i] > 40.0:
            continue
        d, nd = idx[i], idx[i + 1]
        rows.append({"date": str(nd.date()), "prev_ret": round(float(ret.iloc[i]), 2),
                     "gap": round((df.loc[nd, "Open"] / df.loc[d, "Close"] - 1) * 100, 2),
                     "intraday": round((df.loc[nd, "Close"] / df.loc[nd, "Open"] - 1) * 100, 2),
                     "open_to_high": round((df.loc[nd, "High"] / df.loc[nd, "Open"] - 1) * 100, 2),
                     "open_to_low": round((df.loc[nd, "Low"] / df.loc[nd, "Open"] - 1) * 100, 2)})
    return rows


def intraday_next_session():
    """60세션 10분봉: SQQQ 종가가 +5% 이상인 세션 다음 세션의 경로."""
    try:
        from scripts.research import i10_lib as L
        df = L.to10m(L.load5m("SQQQ", "60d"))
    except Exception:
        return {}
    if df is None or not len(df):
        return {}
    out = []
    for sess, g in df.groupby("session"):
        if not len(g):
            continue
        r = (float(g["Close"].iloc[-1]) / float(g["Close"].iloc[0]) - 1) * 100
        out.append((str(sess), r, float(g["Close"].iloc[-1]), g))
    rows = []
    for i in range(len(out) - 1):
        sess, r, close, _ = out[i]
        if r < 5.0:
            continue
        nsess, _, nclose, ng = out[i + 1]
        nopen = float(ng["Open"].iloc[0])
        at10 = ng[ng["hm"] == "10:00"]
        rows.append({"session": nsess, "prev_ret": round(r, 2),
                     "gap": round((nopen / close - 1) * 100, 2),
                     "high_vs_open": round((float(ng["High"].max()) / nopen - 1) * 100, 2),
                     "low_vs_open": round((float(ng["Low"].min()) / nopen - 1) * 100, 2),
                     "close_vs_open": round((nclose / nopen - 1) * 100, 2),
                     "at10_vs_open": (round((float(at10["Close"].iloc[0]) / nopen - 1) * 100, 2)
                                      if len(at10) else None),
                     "hit_plus2_from_open": bool(float(ng["High"].max()) >= nopen * 1.02)})
    return {"events": rows, "n": len(rows)}


def main() -> int:
    qqq, sqqq, soxl = daily("QQQ"), daily("SQQQ"), daily("SOXL")
    ks = daily("IDX_KS11_1d_5y_pre0.pkl", refresh=False)
    kr = gap_events(qqq, sqqq, ks) if len(ks) else []
    kr45 = [r for r in kr if r["kospi_ret"] <= -4.5]
    big = after_big_up(sqqq, 5.0)
    intra = intraday_next_session()

    # 오늘의 위치 — 11-19 SQQQ +6.62%(46.35 → 49.42)
    base = LAST_YAHOO
    options = {
        "A_즉시익절(개장가)": {
            "trigger": "23:30 KST 개장 시장가 매도",
            "expected_px": round(base * (1 + (statistics.mean([r['gap'] for r in big if r['gap']]) / 100
                                             if [r for r in big if r['gap']] else 0.0)), 2),
            "note": "갭 평균을 쓰면 아래 표의 갭 분포가 근거",
        },
        "B_지정가목표(+2%)=50.40": {
            "trigger": f"지정가 $50.40 (현 종가 {base:.2f} × 1.02) + 본전 스탑 $48.34",
            "target": 50.40,
            "fill_prob": round(sum(1 for r in big if r["open_to_high"] >= 2.0) / max(1, len(big)) * 100, 1)
            if big else None,
        },
        "C_현행(05:40 종가청산+샹들리에0.9ATR)": {
            "trigger": "오늘 밤 05:40 KST 세션 종가 청산, 트레일 최고가-0.9×ATR(6.06%×0.9≈5.5%)",
            "note": "트레일 폭 5.5%가 이익 3.11%보다 넓어 실질적으로 05:40 시간청산과 같다",
        },
        "D_변곡청산(신규 모듈)": {
            "trigger": "세션 VWAP 이탈·EMA 역크로스·기저지수 반전 2표 → 청산, 아니면 05:40",
            "note": "10분봉 60세션 A/B 실측 참조(같은 문서 표)",
        },
        "E_하룻밤보유(10:00 청산)": {
            "trigger": "다음 세션 10:00 ET(00:00 KST) 청산",
            "evidence": describe([(r["gap"] + r["intraday"]) for r in big]) if big else {},
        },
    }

    pnl = {
        "1주": {"sell_at_49.42": round(49.42 - ENTRY - ENTRY * RT, 2),
                "sell_at_49.68": round(49.68 - ENTRY - ENTRY * RT, 2),
                "sell_at_46.35(전일종가)": round(46.35 - ENTRY - ENTRY * RT, 2)},
        "3주": {"sell_at_49.42": round((49.42 - ENTRY - ENTRY * RT) * 3, 2),
                "sell_at_49.68": round((49.68 - ENTRY - ENTRY * RT) * 3, 2)},
    }
    doc = {
        "generated_at_kst": now_kst(),
        "position": {"ticker": "SQQQ", "qty": 1, "entry": ENTRY,
                     "close_yahoo": LAST_YAHOO, "close_nh": LAST_NH,
                     "unrealized_usd_yahoo": round(LAST_YAHOO - ENTRY, 2),
                     "unrealized_pct_yahoo": round((LAST_YAHOO / ENTRY - 1) * 100, 2),
                     "prev_close": 46.35, "session_ret_pct": 6.62},
        "kr_crash_days": {"threshold_kospi_pct": -3.0, "n": len(kr),
                          "qqq_gap": describe([r["qqq_gap"] for r in kr]),
                          "qqq_intraday": describe([r["qqq_intraday"] for r in kr]),
                          "sqqq_gap": describe([r["sqqq_gap"] for r in kr]),
                          "sqqq_intraday": describe([r["sqqq_intraday"] for r in kr]),
                          "sqqq_open_to_high": describe([r["sqqq_open_to_high"] for r in kr]),
                          "sqqq_open_to_low": describe([r["sqqq_open_to_low"] for r in kr]),
                          "hit_plus2_from_open_pct": round(sum(1 for r in kr if r["sqqq_open_to_high"] >= 2.0) / max(1, len(kr)) * 100, 1),
                          "events": kr[-12:]},
        "kr_crash_days_deep": {"threshold_kospi_pct": -4.5, "n": len(kr45),
                               "sqqq_gap": describe([r["sqqq_gap"] for r in kr45]),
                               "sqqq_intraday": describe([r["sqqq_intraday"] for r in kr45]),
                               "hit_plus2_from_open_pct": round(sum(1 for r in kr45 if r["sqqq_open_to_high"] >= 2.0) / max(1, len(kr45)) * 100, 1),
                               "events": kr45},
        "after_sqqq_up5": {"n": len(big), "gap": describe([r["gap"] for r in big]),
                           "intraday": describe([r["intraday"] for r in big]),
                           "open_to_high": describe([r["open_to_high"] for r in big]),
                           "open_to_low": describe([r["open_to_low"] for r in big]),
                           "events": big[-10:]},
        "intraday_next_session_after_up5": intra,
        "today_kr_signal": {"level": "WEAK", "kospi_pct": -5.215, "semis_pct": -6.463,
                            "soxl_gap_expected_pct": -3.86, "soxl_gap_sd_pct": 4.248,
                            "r2": 0.196, "source": "core.kr_session_signal (2026-11-20 16:01 KST 계산)"},
        "options": options, "pnl_by_size": pnl,
        "note": ("갭·장중 통계는 5년 일봉(yfinance 재수집), 10분봉은 60세션. 표본이 작아 "
                 "확률로 단정하지 않고 분포(중앙값·사분위)로 읽는다."),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    print(f"[tonight] {doc['generated_at_kst']}")
    print("KOSPI≤-3% 날:", doc["kr_crash_days"]["n"], "건")
    print("  QQQ 갭:", doc["kr_crash_days"]["qqq_gap"])
    print("  QQQ 장중:", doc["kr_crash_days"]["qqq_intraday"])
    print("  SQQQ 갭:", doc["kr_crash_days"]["sqqq_gap"])
    print("  SQQQ 장중:", doc["kr_crash_days"]["sqqq_intraday"])
    print("SQQQ 전일 +5% 다음날:", len(big), "건")
    print("  갭:", doc["after_sqqq_up5"]["gap"])
    print("  장중:", doc["after_sqqq_up5"]["intraday"])
    print("  高:", doc["after_sqqq_up5"]["open_to_high"], "低:", doc["after_sqqq_up5"]["open_to_low"])
    print("10분봉 다음세션:", json.dumps(intra.get("events", []), ensure_ascii=False))
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
