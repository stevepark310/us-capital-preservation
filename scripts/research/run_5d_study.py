#!/usr/bin/env python3
"""(A) 현행 알고리즘 5거래일 10분 단위 백테스트
(B) 급락·급등 변곡점 포착 정밀 진단 (포착/미포착 + 실측 차단 사유)

실행: python3 scripts/research/run_5d_study.py
출력: data/research_10m_5d_20261118.json
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import i10_lib as L                          # noqa: E402
import i10_core_engine as CE                 # noqa: E402
from core import execution_policy            # noqa: E402

DATA = os.path.join(L.BASE_DIR, "data")
DAYS = L.SESSION_DAYS
OUT = os.path.join(DATA, "research_10m_5d_20261118.json")


# ------------------------------------------------------------------ zigzag
def zigzag(close: pd.Series, thr_pct: float) -> List[Dict[str, Any]]:
    """종가 지그재그 — thr% 이상 반전으로 국소 고점/저점 확정."""
    c = list(map(float, close.values))
    idx = list(close.index)
    n = len(c)
    pts: List[Dict[str, Any]] = []
    if n < 3:
        return pts
    trend, ext, ext_i = 0, c[0], 0
    for i in range(1, n):
        if trend >= 0:
            if c[i] > ext:
                ext, ext_i = c[i], i
            elif (ext - c[i]) / ext * 100.0 >= thr_pct:
                pts.append({"kind": "HIGH", "i": ext_i, "ts": idx[ext_i], "price": ext})
                trend, ext, ext_i = -1, c[i], i
        if trend <= 0:
            if c[i] < ext:
                ext, ext_i = c[i], i
            elif (c[i] - ext) / ext * 100.0 >= thr_pct:
                pts.append({"kind": "LOW", "i": ext_i, "ts": idx[ext_i], "price": ext})
                trend, ext, ext_i = 1, c[i], i
    return pts


def legs_from(points: List[Dict[str, Any]], g: pd.DataFrame) -> List[Dict[str, Any]]:
    legs = []
    for a, b in zip(points, points[1:]):
        if a["kind"] == b["kind"] or b["i"] <= a["i"]:
            continue
        move = (b["price"] / a["price"] - 1) * 100
        legs.append({"from_ts": str(a["ts"]), "from_hm": g.loc[a["ts"], "hm_end"], "from_px": round(a["price"], 3),
                     "to_ts": str(b["ts"]), "to_hm": g.loc[b["ts"], "hm_end"], "to_px": round(b["price"], 3),
                     "dir": "UP" if move > 0 else "DOWN", "move_pct": round(move, 2),
                     "bars": int(b["i"] - a["i"])})
    return legs


def build_daily_signals(days: List[str], dm: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for d in days:
        out[d] = {}
        dday = pd.Timestamp(d)
        for tk in ("SOXL", "TQQQ"):
            tab = dm.get(tk)
            if tab is None:
                continue
            prev = tab[tab.index < dday]
            if not len(prev):
                continue
            row = prev.iloc[-1]
            ok = (float(row["rsi2"]) <= 20.0 and float(row["pct_b"]) <= 0.15 and float(row["rvol"]) >= 1.0)
            out[d][tk] = {"as_of": str(prev.index[-1])[:10], "rsi2": round(float(row["rsi2"]), 1),
                          "pct_b": round(float(row["pct_b"]), 3), "rvol": round(float(row["rvol"]), 2),
                          "atr_pct": round(float(row["atr_pct"]), 2), "signal": bool(ok)}
    return out


def _decision_end(ts: str) -> pd.Timestamp:
    return pd.Timestamp(ts) + pd.Timedelta(minutes=10)


def diagnose_legs(day: str, sym: str, legs: List[Dict[str, Any]], panel: Dict[str, pd.DataFrame],
                  panel5: Dict[str, pd.DataFrame], infl: Dict[str, Any], trades: List[Dict[str, Any]],
                  vix10: pd.Series, dxy10: pd.Series, sentiment: Optional[float],
                  budget: float = 150.0) -> List[Dict[str, Any]]:
    """각 leg를 현행 알고리즘이 잡았는지 + 못 잡았다면 실측 차단 사유."""
    out = []
    inv = {"SOXL": "SOXS", "TQQQ": "SQQQ", "SOXS": "SOXL", "SQQQ": "TQQQ"}
    for leg in legs:
        if abs(leg["move_pct"]) < 2.5:
            continue
        instrument = sym if leg["dir"] == "UP" else inv.get(sym)
        if instrument is None:
            continue
        # leg 구간과 겹친 현행 엔진 거래
        hit = None
        for t in trades:
            if t["reason"].startswith("OPEN_AT_WINDOW_END"):
                continue
            if t["ticker"] != instrument:          # 방향이 맞는 종목(롱=정방향, 숏=인버스)만 인정
                continue
            if leg["from_ts"] <= t["entry_ts"] <= leg["to_ts"]:
                hit = t
                break
        px_instr = px_out = None
        b = panel.get(instrument)
        if b is not None:
            sub = b[b["session"] == day]
            if len(sub):
                near = sub[sub.index <= pd.Timestamp(leg["from_ts"])]
                if len(near):
                    px_instr = float(near["Close"].iloc[-1])
                near2 = sub[sub.index <= pd.Timestamp(leg["to_ts"])]
                if len(near2):
                    px_out = float(near2["Close"].iloc[-1])
        leg_usd = (abs((px_out / px_instr - 1) * 100) if (px_instr and px_out) else abs(leg["move_pct"])) / 100 * budget
        # 피벗 시점 실측값
        dec = _decision_end(leg["from_ts"])
        m5 = CE.metrics_at(panel5.get(sym), dec) or {}
        vix = float(vix10[vix10.index < dec].iloc[-1]) if len(vix10[vix10.index < dec]) else None
        dxy = float(dxy10[dxy10.index < dec].iloc[-1]) if len(dxy10[dxy10.index < dec]) else None
        conf = 0
        if vix is not None and vix >= 24.0:
            if dxy is not None and dxy >= 104.5:
                conf += 1
            if sentiment is not None and sentiment < 40:
                conf += 1
        hm_end = (pd.Timestamp(dec)).strftime("%H:%M")
        reasons: List[str] = []
        if hit:
            reasons.append("포착")
        else:
            if leg["dir"] == "UP":
                dip_ok = (m5.get("rsi2", 99) <= 20 and m5.get("pct_b", 9) <= 0.15 and m5.get("rvol", 0) >= 1.0)
                if hm_end <= "10:00":
                    reasons.append("개장 유예(23:30~00:00 KST) — 반등 시작 봉이 유예 안")
                elif dip_ok:
                    reasons.append("DIP 조건 충족했으나 세션 3건 한도/쿨다운/보유중으로 미집행")
                else:
                    fails = []
                    if m5.get("rsi2", 99) > 20:
                        fails.append(f"RSI2 {m5.get('rsi2'):.1f}>20")
                    if m5.get("pct_b", 9) > 0.15:
                        fails.append(f"%B {m5.get('pct_b'):.2f}>0.15")
                    if m5.get("rvol", 0) < 1.0:
                        fails.append(f"RVOL {m5.get('rvol'):.2f}<1.0")
                    reasons.append("DIP 필터 미충족: " + ", ".join(fails))
            else:
                if vix is None or vix < 24.0:
                    reasons.append(f"BEAR_ALPHA 미발동: VIX {vix:.1f} < 24.0" if vix else "BEAR_ALPHA 미발동: VIX 미확인")
                else:
                    reasons.append(f"BEAR_ALPHA 확인 {conf}/2 (VIX {vix:.1f}, DXY {dxy:.2f} < 104.5)" if dxy and dxy < 104.5
                                   else f"BEAR_ALPHA 확인 {conf}/2")
        out.append({"day": day, "instrument": instrument, "dir": leg["dir"], "from_hm": leg["from_hm"],
                    "from_px": leg["from_px"], "to_hm": leg["to_hm"], "to_px": leg["to_px"],
                    "move_pct": leg["move_pct"], "leg_usd_at_150": round(leg_usd, 2),
                    "captured": bool(hit), "trade": ({"ticker": hit["ticker"], "entry_ts": hit["entry_ts"],
                                                     "exit_ts": hit["exit_ts"], "ret_pct": hit["ret_pct"],
                                                     "pnl_usd": hit["pnl_usd"]} if hit else None),
                    "instr_price": round(px_instr, 2) if px_instr else None,
                    "vix": round(vix, 2) if vix else None, "dxy": round(dxy, 2) if dxy else None,
                    "m5": {k: round(v, 2) for k, v in m5.items()}, "why_not": "; ".join(reasons)})
    return out


def main() -> int:
    panel = L.panel(period="60d")
    panel5 = {s: CE.five_min_metrics(CE.L.load5m(s, "60d")) for s in L.SYMBOLS}
    vix10 = panel["^VIX"]["Close"]
    dxy10 = L.to10m(L.load5m("DX-Y.NYB", "60d"))["Close"]
    daily = CE.daily_bars(["SOXL", "TQQQ", "SOXS", "SQQQ", "QQQ", "NVDA"])
    dm = {k: CE.daily_metrics(v) for k, v in daily.items()}
    daily_sig = build_daily_signals(DAYS, dm)

    sentiment = None
    try:
        sentiment = float(json.load(open(os.path.join(DATA, "news_sentiment_report.json"))).get("average_sentiment_score"))
    except Exception:
        pass

    policy = execution_policy.load_policy()
    params = json.load(open(os.path.join(DATA, "strategy_params.json")))

    kw = dict(vix10=vix10, dxy10=dxy10, sentiment=sentiment, policy=policy, params=params)
    core_cur = CE.sim_core(DAYS, panel, panel5, daily_sig, budget_mode="current", label="현행(예산 $150)", **kw)
    core_leg = CE.sim_core(DAYS, panel, panel5, daily_sig, budget_mode="legacy", label="구예산($44.8)", **kw)
    core_slip = CE.sim_core(DAYS, panel, panel5, daily_sig, budget_mode="current", slippage_bps=12.0,
                            label="현행+슬리피지 12bp/편도", **kw)

    # ---------------------------------------------------------------- B) 변곡점
    THR = {"SOXL": 1.5, "TQQQ": 1.0, "SOXS": 1.5, "SQQQ": 1.0, "QQQ": 0.6, "NVDA": 1.0}
    infl: Dict[str, Any] = {}
    for d in DAYS:
        infl[d] = {}
        for sym, thr in THR.items():
            b = panel.get(sym)
            if b is None:
                continue
            g = b[b["session"] == d]
            if len(g) < 3:
                continue
            pts = zigzag(g["Close"], thr)
            legs = legs_from(pts, g)
            piv = []
            for p in pts:
                ts = p["ts"]
                dec = _decision_end(str(ts))
                vix = float(vix10[vix10.index < dec].iloc[-1]) if len(vix10[vix10.index < dec]) else None
                dxy = float(dxy10[dxy10.index < dec].iloc[-1]) if len(dxy10[dxy10.index < dec]) else None
                conf = 0
                if vix is not None and vix >= 24.0:
                    if dxy is not None and dxy >= 104.5:
                        conf += 1
                    if sentiment is not None and sentiment < 40:
                        conf += 1
                m5 = CE.metrics_at(panel5.get(sym), dec)
                piv.append({"ts": str(ts), "hm": g.loc[ts, "hm_end"], "close_ts": dec.strftime("%H:%M"),
                            "kind": p["kind"], "price": round(p["price"], 3),
                            "vix": round(vix, 2) if vix else None, "dxy": round(dxy, 2) if dxy else None,
                            "bear_confirmations": conf,
                            "m5": {k: round(v, 2) for k, v in (m5 or {}).items()} if m5 else None})
            infl[d][sym] = {"threshold_pct": thr, "pivots": piv, "legs": legs,
                            "open": round(float(g["Close"].iloc[0]), 3),
                            "close": round(float(g["Close"].iloc[-1]), 3),
                            "high": round(float(g["High"].max()), 3), "low": round(float(g["Low"].min()), 3)}

    capture: Dict[str, Any] = {}
    for d in DAYS:
        rows: List[Dict[str, Any]] = []
        for sym in ("SOXL", "TQQQ", "QQQ", "NVDA"):
            rows += diagnose_legs(d, sym, infl[d][sym]["legs"], panel, panel5, infl[d], core_cur["trades"],
                                  vix10, dxy10, sentiment)
        # 중복 제거(같은 leg의 역방향 중복) — SOXL/SOXS 는 같은 움직임이므로 SOXL·TQQQ·QQQ·NVDA 기준만
        seen = set()
        uniq = []
        for r in sorted(rows, key=lambda x: (-abs(x["move_pct"]))):
            key = (r["from_hm"], r["dir"] if r["instrument"] in ("SOXL", "TQQQ", "QQQ", "NVDA") else r["instrument"] + r["dir"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        capture[d] = sorted(uniq, key=lambda x: x["from_hm"])

    # 오라클: 10분 지그재그(0.8%) 완전예지 — 정방향 롱/인버스 숏, 왕복 0.24%, $150
    oracle: Dict[str, Any] = {}
    for d in DAYS:
        tot, detail = 0.0, []
        for sym, inv in (("SOXL", "SOXS"), ("TQQQ", "SQQQ")):
            g = panel[sym][panel[sym]["session"] == d]
            for leg in legs_from(zigzag(g["Close"], 0.8), g):
                ins = sym if leg["dir"] == "UP" else inv
                gg = panel[ins][panel[ins]["session"] == d]
                s_map = {str(ts): float(px) for ts, px in gg["Close"].items()}
                a, b = s_map.get(leg["from_ts"]), s_map.get(leg["to_ts"])
                if not a or not b:
                    continue
                ret = (b / a - 1) * 100 - L.RT_COST * 100
                usd = ret / 100 * 150.0
                tot += usd
                detail.append({"leg": f"{leg['from_hm']}→{leg['to_hm']}", "instrument": ins,
                               "ret_pct": round(ret, 2), "usd_at_150": round(usd, 2)})
        oracle[d] = {"usd_at_150_8pct_threshold": round(tot, 2), "legs": len(detail), "detail": detail}

    out = {
        "generated_at_kst": pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S"),
        "window": DAYS, "cost_round_trip_pct": L.RT_COST * 100,
        "account": {"equity_usd": 883.5, "cash_usd": 636.0},
        "policy_snapshot": execution_policy.describe(policy, 883.5, 636.0, vix=18.9, extra_scale=0.7),
        "daily_signals": daily_sig, "sentiment_assumed": sentiment,
        "core_current": {k: v for k, v in core_cur.items() if k != "equity_curve"},
        "core_legacy_44_8": {k: v for k, v in core_leg.items() if k != "equity_curve"},
        "core_current_slippage_12bp": {k: v for k, v in core_slip.items() if k != "equity_curve"},
        "inflections": infl, "capture": capture, "oracle": oracle,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved", OUT)
    for k in ("core_current", "core_legacy_44_8", "core_current_slippage_12bp"):
        s = out[k]["stats"]
        print(f"{k}: n={s['n']} pnl=${s['pnl_usd']} win={s['win_pct']}% pf={s['pf']} mdd=${s['mdd_usd']} "
              f"fee=${s['fee_usd']} fee_share={s['fee_share_pct']}% avg={s['avg_pct']}%")
        print("   per_day:", {d: v["realized_usd"] for d, v in out[k]["per_day"].items()})
    print("oracle:", {d: v["usd_at_150_8pct_threshold"] for d, v in oracle.items()})
    print("captured legs:", sum(1 for d in DAYS for r in capture[d] if r["captured"]),
          "/", sum(1 for d in DAYS for r in capture[d]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
