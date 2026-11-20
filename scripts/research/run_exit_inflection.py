#!/usr/bin/env python3
"""
장중 추세 변곡(inflection) 청산 A/B + 포지션 크기·방어책 — 인버스 포지션(SQQQ·SOXS).

    python3 scripts/research/run_exit_inflection.py            # 전체 + JSON
    python3 scripts/research/run_exit_inflection.py --no-refresh

질문 (동훈님 2026-11-20 지시)
------------------------------
"장중 추세 변곡 시점을 잡을 수 있는 방안을 만들어" · "하락장에서도 수익을 낼 수 있게"
"수량도 좀 더 늘려도 괜찮을 것 같아 다만 헷징 등 다양한 방어책을 두면서"

답하는 것
---------
1. **청산 규칙 A/B** — 같은 진입 집합에 청산 규칙만 바꾼다(진입 엣지와 청산 엣지 분리).
2. **포지션 크기 × 방어책** — 1·2·3·4주 / 자산 10·15·20% / ATR 리스크 타깃 ×
   하드 손절·일일 손실한도·SGOV 바벨. 최악 1회 손실($), MDD(%), $25 초과 위험까지.
3. **+2·+3% 평가익 상태에서 각 규칙의 기대 결과** — 오늘 밤 SQQQ 1주 판단용.
4. **레짐 분해** — 같은 규칙을 QQQ 추세 상승/하락 구간으로 나눠 본다(표본 편향 방지).

규약
----
* 10분봉(5분봉 리샘플, 정규장 09:30~16:00 ET), yfinance 60일. 2026-11-20 재수집.
* 결정은 그 봉 종가까지의 정보 → 체결도 그 봉 종가(룩어헤드 없음).
* 왕복 비용 0.24%(편도 12bp), 정수주, 세션 종가(15:40 ET = 05:40 KST) 강제청산.
* IS/OOS = 세션 30/30 분할. 파라미터는 IS에서만 고르고 OOS는 그대로 읽는다.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

import numpy as np  # noqa: E402

from core import intraday_inflection_exit as inf  # noqa: E402
from scripts.research import i10_lib as L  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
OUT = os.path.join(DATA, "research_exit_inflection_20261120.json")
KST = timedelta(hours=9)

RT_COST = 0.0024
BUDGET = 150.0
EQUITY = 877.98
WEEKLY_LOSS_CAP = 25.0
FORCE_HM = "15:30"      # 15:30~15:40 봉 종료 = 15:40 ET = 05:40 KST
SYMBOLS = ["SQQQ", "SOXS", "SOXL", "TQQQ", "QQQ", "^VIX"]
PAIRS = {"SQQQ": "QQQ", "SOXS": "SOXL"}


def now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ 데이터
def load_panel(refresh: bool) -> dict:
    out = {}
    for s in SYMBOLS:
        df = L.to10m(L.load5m(s, "60d", refresh=refresh))
        if len(df):
            out[s] = df
    return out


class Book:
    """한 종목의 10분봉 → 판정용 컨텍스트(전체 기간) + (session, hm) 색인."""

    def __init__(self, df, params=None):
        self.df = df
        self.session = [str(x) for x in df["session"]]
        self.hm = [str(x) for x in df["hm"]]
        self.close = df["Close"].to_numpy(float)
        self.high = df["High"].to_numpy(float)
        self.low = df["Low"].to_numpy(float)
        self.ctx = inf.build_context({
            "close": list(self.close), "high": list(self.high), "low": list(self.low),
            "volume": list(df["Volume"].to_numpy(float)), "session": list(self.session),
        }, params)
        self.by_session = {}
        for i, s in enumerate(self.session):
            self.by_session.setdefault(s, []).append(i)
        self.session_list = list(self.by_session.keys())
        self.key = {(self.session[i], self.hm[i]): i for i in range(len(self.session))}

    def idx(self, session: str, hm: str):
        return self.key.get((session, hm))


def align_underlying(book: Book, und: Book) -> list:
    """종목 인덱스 → 기저지수 인덱스 매핑(봉 누락이 있어도 시각으로 맞춘다)."""
    return [und.key.get((book.session[i], book.hm[i]), -1) for i in range(len(book.session))]


# ------------------------------------------------------------------ 진입 집합
def gen_entries(panel: dict) -> dict:
    books = {k: Book(v) for k, v in panel.items()}
    sessions = sorted(set(books["SQQQ"].session_list) & set(books["QQQ"].session_list))
    fam: dict[str, list] = {"c5_inv": [], "vwap_break_inv": [], "late_spike_inv": [], "null_1200": []}
    prev_close = {}
    for s in sessions:
        for tk in ("QQQ", "SOXL", "SQQQ", "SOXS"):
            b = books.get(tk)
            if b and s in b.by_session and b.by_session[s][0] > 0:
                prev_close[(tk, s)] = float(b.close[b.by_session[s][0] - 1])

    for s in sessions:
        for inv_tk, und_tk in PAIRS.items():
            bi, bu = books.get(inv_tk), books.get(und_tk)
            if not bi or not bu or s not in bi.by_session or s not in bu.by_session:
                continue
            pc_u, pc_i = prev_close.get((und_tk, s)), prev_close.get((inv_tk, s))
            # (1) C5: 10:00 ET 방향이 아래면 인버스
            j, k = bu.idx(s, "10:00"), bi.idx(s, "10:00")
            if j is not None and k is not None and pc_u and bu.close[j] < pc_u:
                fam["c5_inv"].append({"ticker": inv_tk, "underlying": und_tk, "session": s, "idx": k})
            # (2) 세션 VWAP 하향 이탈 — 10:00~14:30 첫 이탈
            for jj in bu.by_session[s]:
                hm = bu.hm[jj]
                if hm < "10:00" or hm > "14:30" or jj <= bu.by_session[s][0]:
                    continue
                if bu.close[jj - 1] >= bu.ctx["vwap"][jj - 1] and bu.close[jj] < bu.ctx["vwap"][jj]:
                    kk = bi.idx(s, hm)
                    if kk is not None:
                        fam["vwap_break_inv"].append({"ticker": inv_tk, "underlying": und_tk,
                                                      "session": s, "idx": kk})
                    break
            # (3) 후반 급등 추격 — 11:00~15:00, 세션 신고가 갱신 중, 전일 대비 +2% 이상
            seen = False
            for jj in bi.by_session[s][1:]:
                hm, start = bi.hm[jj], bi.by_session[s][0]
                if seen or hm < "11:00" or hm > "15:00":
                    continue
                run_max = float(np.max(bi.high[start:jj + 1]))
                up = (bi.close[jj] / pc_i - 1) * 100 if pc_i else 0.0
                if abs(bi.close[jj] - run_max) < 1e-9 and up >= 2.0:
                    fam["late_spike_inv"].append({"ticker": inv_tk, "underlying": und_tk,
                                                  "session": s, "idx": jj})
                    seen = True
            # (4) 널 계열 — 12:00 고정 진입
            kk = bi.idx(s, "12:00")
            if kk is not None:
                fam["null_1200"].append({"ticker": inv_tk, "underlying": und_tk, "session": s, "idx": kk})
    return {"books": books, "families": fam, "sessions": sessions}


# ------------------------------------------------------------------ 청산 규칙
def _atr_pct(ctx, i):
    v = float(ctx["atr_pct"][i])
    return v if np.isfinite(v) and v > 0 else 6.0


def sim_trade(book: Book, und: Book | None, uidx: list, e: dict, rule: str,
              params: dict | None = None, entry_px: float | None = None,
              start_idx: int | None = None, qty: int = 1,
              sl_pct_override: float | None = None) -> dict:
    """한 진입에 청산 규칙 하나. `entry_px`/`start_idx`는 부분익절·상태분석용 재개 지점."""
    p = dict(inf.DEFAULT_PARAMS)
    p.update(params or {})
    i0 = int(e["idx"])
    entry = float(entry_px if entry_px else book.close[i0])
    s0 = book.session[i0]
    start = int(start_idx if start_idx is not None else i0)
    horizon = "same_session"
    if rule in ("hold_close_carry",):
        horizon = "next_session"
    path = []
    for j in range(start + 1, len(book.close)):
        sess = book.session[j]
        if sess == s0:
            path.append(j)
        elif horizon == "next_session" and sess > s0:
            path.append(j)
        elif sess > s0:
            break
    if not path:
        return {}
    atr0 = _atr_pct(book.ctx, i0)
    atr_abs = entry * atr0 / 100.0
    hh = float(np.max(book.high[min(i0, start):start + 1]))
    mae = 0.0
    mfe = 0.0
    if sl_pct_override:
        sl = round(entry * (1 - float(sl_pct_override) / 100.0), 2)
    else:
        sl = round(entry * (1 - min(max(0.75 * atr0, 2.0), 8.0) / 100.0), 2)
    tp = round(entry * (1 + 2.5 / 100.0), 2)
    exit_px = reason = None
    j_exit = None
    trail = None
    half_done = False
    half_pnl = 0.0
    sl_disaster = round(entry * 0.94, 2)
    for j in path:
        px = float(book.close[j])
        hh = max(hh, float(book.high[j]))
        mfe = max(mfe, (hh / entry - 1) * 100)
        mae = min(mae, (float(book.low[j]) / entry - 1) * 100)
        hm = book.hm[j]
        new_session = book.session[j] != book.session[path[0]]
        if horizon == "next_session" and (new_session and hm > "10:00"):
            exit_px, reason, j_exit = px, "NEXT_OPEN", j
            break
        if rule == "hold_close" or rule == "hold_close_carry":
            if horizon == "same_session" and hm >= FORCE_HM:
                exit_px, reason, j_exit = px, "SESSION", j
                break
        elif rule == "fixed_tp_sl":
            if px <= sl:
                exit_px, reason, j_exit = px, "SL", j
                break
            if px >= tp:
                exit_px, reason, j_exit = px, "TP", j
                break
            if hm >= FORCE_HM:
                exit_px, reason, j_exit = px, "SESSION", j
                break
        elif rule in ("chandelier09", "chandelier15"):
            k = 0.9 if rule == "chandelier09" else 1.5
            if px <= sl:
                exit_px, reason, j_exit = px, "SL", j
                break
            trail = hh - k * atr_abs
            if hh >= entry + atr_abs:
                trail = max(trail, entry * 1.0034)
            if px <= trail and px > entry:
                exit_px, reason, j_exit = px, "TRAIL", j
                break
            if hm >= FORCE_HM:
                exit_px, reason, j_exit = px, "SESSION", j
                break
        elif rule in ("be_only", "be_trail2", "disaster6", "staged"):
            # 방어책 변형 — 손익비가 아니라 '이익 잠금'만 하는 규칙
            if px <= sl_disaster:
                exit_px, reason, j_exit = px, "DISASTER_SL", j
                break
            if rule == "be_only" and hh >= entry + max(atr_abs, entry * 0.01):
                be = max(sl, entry * 1.0034)
                if px <= be:
                    exit_px, reason, j_exit = px, "BREAKEVEN", j
                    break
            if rule == "be_trail2" and hh >= entry + max(atr_abs, entry * 0.01):
                tr = max(hh - 2.0 * atr_abs, entry * 1.0034)
                if px <= tr:
                    exit_px, reason, j_exit = px, "TRAIL2", j
                    break
            if rule == "staged":
                if not half_done and px >= entry * 1.025:
                    half_done = True
                    half_pnl = (px / entry - 1) * 100 * 0.5            # 절반 익절(수량 2주 이상 전제)
                if half_done and px <= sl_disaster:
                    exit_px, reason, j_exit = px, "DISASTER_SL", j
                    break
            if hm >= FORCE_HM:
                exit_px, reason, j_exit = px, "SESSION", j
                break
        else:                                     # inflection 계열
            cfg = dict(p)
            if rule == "inflection_trail_only":
                cfg["votes_required"] = 9
            elif rule == "inflection_struct_only":
                cfg["trail_atr_mult"] = 99.0
                cfg["giveback_min_pct"] = 99.0
                cfg["be_min_pct"] = 99.0
                cfg["be_after_atr"] = 99.0
            uctx = und.ctx if und is not None else None
            dec = inf.evaluate({"entry_price": entry, "bought_qty": qty, "sl": sl,
                                "atr_pct_at_plan": atr0},
                               book.ctx, j, cfg, underlying_ctx=uctx, entry_index=i0,
                               underlying_i=uidx[j] if uidx else None)
            if dec:
                exit_px, reason, j_exit = px, dec.get("signal") or "EXIT", j
                break
            if hm >= FORCE_HM:
                exit_px, reason, j_exit = px, "SESSION", j
                break
    if exit_px is None:
        proxy = [j for j in path if book.session[j] == s0] or path
        j_exit = proxy[-1]
        exit_px, reason = float(book.close[j_exit]), "SESSION_LAST"
    pct = (exit_px / entry - 1) * 100 - RT_COST * 100
    if half_done:
        pct = half_pnl + ((exit_px / entry - 1) * 100 - RT_COST * 100) * 0.5
    return {"ticker": e["ticker"], "session": s0, "hm": book.hm[i0],
            "entry": round(entry, 2), "exit": round(exit_px, 2), "pct": round(pct, 3),
            "reason": reason, "hold_min": (j_exit - start) * 10 if j_exit else None,
            "bars": int(j_exit - start) if j_exit else 0,
            "mae_pct": round(mae, 2), "mfe_pct": round(mfe, 2), "atr_pct": round(atr0, 2)}


def metrics(trades: list, qty=1) -> dict:
    tr = [t for t in trades if t.get("pct") is not None]
    if not tr:
        return {"n": 0}
    pcts = [t["pct"] for t in tr]
    if qty == "budget":
        pnls = [t["pct"] / 100 * t["entry"] * max(1, int(BUDGET // t["entry"])) for t in tr]
    else:
        pnls = [t["pct"] / 100 * t["entry"] * int(qty) for t in tr]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    cum = peak = mdd = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    sd = statistics.pstdev(pcts) if len(pcts) > 1 else 0.0
    t_stat = (statistics.mean(pcts) / (sd / len(pcts) ** 0.5)) if sd > 0 else 0.0
    cap_trips = 0
    by_day = {}
    for t in tr:
        by_day.setdefault(t["session"], []).append(t)
    for day, ts in by_day.items():
        s = sum(x["pct"] / 100 * x["entry"] * (max(1, int(BUDGET // x["entry"])) if qty == "budget" else int(qty))
                for x in ts)
        if s < -WEEKLY_LOSS_CAP:
            cap_trips += 1
    return {
        "n": len(tr), "ev_pct": round(statistics.mean(pcts), 3),
        "median_pct": round(statistics.median(pcts), 3),
        "win_pct": round(len(wins) / len(tr) * 100, 1),
        "pnl_usd": round(sum(pnls), 2), "pf": round(pf, 2) if pf else None,
        "worst_usd": round(min(pnls), 2), "best_usd": round(max(pnls), 2),
        "mdd_usd": round(mdd, 2), "mdd_pct_of_equity": round(mdd / EQUITY * 100, 2),
        "t": round(t_stat, 2), "avg_hold_min": round(statistics.mean([t["hold_min"] or 0 for t in tr])),
        "avg_mae_pct": round(statistics.mean([t["mae_pct"] for t in tr]), 2),
        "avg_mfe_pct": round(statistics.mean([t["mfe_pct"] for t in tr]), 2),
        "capture_pct": round(statistics.mean(
            [(t["pct"] / t["mfe_pct"] * 100) if t["mfe_pct"] > 0.2 else 0 for t in tr]), 1),
        "days_over_25usd_loss": cap_trips,
        "reason_mix": {r: sum(1 for t in tr if t["reason"] == r) for r in
                       sorted({t["reason"] for t in tr})},
    }


# ------------------------------------------------------------------ 상태 분석
def profit_state_analysis(books, families, rules, uidxs) -> dict:
    """진입 후 +2.0/+2.5/+3.0% 도달 시점부터 각 규칙의 기대 결과(오늘 밤 판단)."""
    out = {}
    for th in (2.0, 2.5, 3.0, 4.0):
        rows = {r: [] for r in rules}
        for fam, ents in families.items():
            if fam == "null_1200":
                continue
            for e in ents:
                book, und = books[e["ticker"]], books.get(e["underlying"])
                i0 = e["idx"]
                entry = float(book.close[i0])
                s0 = book.session[i0]
                hit = next((j for j in book.by_session[s0] if j > i0
                            and (book.high[j] / entry - 1) * 100 >= th), None)
                if hit is None:
                    continue
                now_pct = (book.close[hit] / entry - 1) * 100 - RT_COST * 100
                for r in rules:
                    t = sim_trade(book, und, uidxs[e["ticker"]], e, r,
                                  entry_px=entry, start_idx=hit)
                    if not t:
                        continue
                    rows[r].append({"sell_now": now_pct, "rule": t["pct"],
                                    "reason": t["reason"], "hold": t["hold_min"]})
        base = rows.get(rules[0]) or []
        out[f"{th}"] = {
            "n": len(base),
            "sell_now_ev_pct": round(statistics.mean([x["sell_now"] for x in base]), 2) if base else None,
            "rules": {r: {
                "ev_pct": round(statistics.mean([x["rule"] for x in v]), 2),
                "delta_vs_now_pct": round(statistics.mean([x["rule"] - x["sell_now"] for x in v]), 2),
                "win_pct": round(sum(1 for x in v if x["rule"] > 0) / len(v) * 100, 1),
                "beat_sell_now_pct": round(sum(1 for x in v if x["rule"] > x["sell_now"]) / len(v) * 100, 1),
                "avg_hold_min": round(statistics.mean([x["hold"] or 0 for x in v])),
            } for r, v in rows.items() if v},
        }
    return out


# ------------------------------------------------------------------ 레짐 분해
def regime_tags(books, sessions) -> dict:
    """세션별 태그 — 10분봉 창의 QQQ 20세션 수익률 부호 / VIX 수준."""
    q, v = books["QQQ"], books.get("^VIX")
    tags = {}
    for i, s in enumerate(sessions):
        idxs = q.by_session.get(s, [])
        if not idxs:
            continue
        c = float(q.close[idxs[-1]])
        if i >= 20:
            p_idx = q.by_session.get(sessions[i - 20], [])
            trend_up = c > float(q.close[p_idx[-1]]) if p_idx else True
        else:
            trend_up = True
        vix = None
        if v is not None and v.by_session.get(s):
            vix = float(v.close[v.by_session[s][0]])
        tags[s] = {"trend_up_20d": trend_up, "vix_open": vix}
    return tags


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    refresh = "--no-refresh" not in (argv or sys.argv)
    panel = load_panel(refresh)
    built = gen_entries(panel)
    books, families, sessions = built["books"], built["families"], built["sessions"]
    uidxs = {tk: align_underlying(books[tk], books[und]) for tk, und in PAIRS.items() if tk in books and und in books}
    tags = regime_tags(books, sessions)
    is_cut = sessions[len(sessions) // 2]

    rules = ["hold_close", "hold_close_carry", "fixed_tp_sl", "chandelier09", "chandelier15",
             "inflection_v1(2표)", "inflection(votes=1)", "inflection(votes=3)",
             "inflection_trail_only", "inflection_struct_only", "profit_lock(모듈)",
             "be_only", "be_trail2", "disaster6", "staged"]

    RULE_IMPL = {"profit_lock(모듈)": ("inflection_v1", inf.preset("profit_lock")),
                 "inflection_v1(2표)": ("inflection_v1", None),
                 "inflection(votes=1)": ("inflection_v1", {"votes_required": 1}),
                 "inflection(votes=3)": ("inflection_v1", {"votes_required": 3}),
                 "inflection_trail_only": ("inflection_trail_only", None),
                 "inflection_struct_only": ("inflection_struct_only", None)}

    def run(ents, rule):
        base, pr = RULE_IMPL.get(rule, (rule, None))
        out = []
        for e in ents:
            t = sim_trade(books[e["ticker"]], books.get(e["underlying"]),
                          uidxs.get(e["ticker"]), e, base, pr,
                          sl_pct_override=(6.0 if rule.startswith("profit_lock") else None))
            if t:
                out.append(t)
        return out

    results = {}
    for fam, ents in families.items():
        results[fam] = {"entries": len(ents)}
        for rule in rules:
            tr = run(ents, rule)
            up = [t for t in tr if tags.get(t["session"], {}).get("trend_up_20d")]
            dn = [t for t in tr if not tags.get(t["session"], {}).get("trend_up_20d")]
            results[fam][rule] = {
                "all": metrics(tr),
                "is": metrics([t for t in tr if t["session"] < is_cut]),
                "oos": metrics([t for t in tr if t["session"] >= is_cut]),
                "uptrend_sessions": metrics(up), "downtrend_sessions": metrics(dn),
                "by_size": {f"{q}주": metrics(tr, q) for q in (1, 2, 3, 4)},
            }

    sens = {}
    for votes in (1, 2, 3):
        for k in (1.0, 1.2, 1.5):
            for be in (0.75, 1.0, 1.5):
                tr = []
                for fam in ("c5_inv", "vwap_break_inv", "late_spike_inv"):
                    for e in families[fam]:
                        t = sim_trade(books[e["ticker"]], books.get(e["underlying"]),
                                      uidxs.get(e["ticker"]), e, "inflection_v1",
                                      {"votes_required": votes, "trail_atr_mult": k, "be_after_atr": be})
                        if t:
                            tr.append(t)
                sens[f"votes={votes},k={k},be={be}"] = {
                    "all": {kk: vv for kk, vv in metrics(tr).items() if kk in ("n", "ev_pct", "win_pct", "pf", "pnl_usd", "mdd_usd", "t")},
                    "is": {kk: vv for kk, vv in metrics([t for t in tr if t["session"] < is_cut]).items() if kk in ("n", "ev_pct", "pf")},
                    "oos": {kk: vv for kk, vv in metrics([t for t in tr if t["session"] >= is_cut]).items() if kk in ("n", "ev_pct", "pf")},
                }

    cond = profit_state_analysis(books, families, ["hold_close", "fixed_tp_sl", "chandelier09",
                                                   "inflection_v1(2표)"], uidxs)
    doc = {
        "generated_at_kst": now_kst(),
        "data": {"symbols": SYMBOLS,
                 "sessions": len(sessions),
                 "from": sessions[0] if sessions else None, "to": sessions[-1] if sessions else None,
                 "is_cut": is_cut, "bar": "10분봉(5분봉 리샘플)",
                 "fee_round_trip_pct": RT_COST * 100, "budget_usd": BUDGET,
                 "force_exit": "15:40 ET = 05:40 KST"},
        "entries": {k: len(v) for k, v in families.items()},
        "regime_counts": {"uptrend_sessions": sum(1 for t in tags.values() if t["trend_up_20d"]),
                          "downtrend_sessions": sum(1 for t in tags.values() if not t["trend_up_20d"])},
        "results": results, "sensitivity_inflection": sens, "profit_state": cond,
        "live_position": {"entry": 48.18, "yahoo_close": 49.42, "nh_close": 49.68,
                          "pnl_1share_yahoo": round(49.42 - 48.18 - 48.18 * RT_COST, 2),
                          "return_pct_yahoo": round((49.42 / 48.18 - 1) * 100, 2)},
        "note": ("결정은 봉 종가 정보로만, 체결도 그 봉 종가. 같은 봉에서 SL·TP 동시 도달은 SL 우선. "
                 "진입 집합은 규칙과 무관하게 고정(청산 규칙만의 효과)."),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    print(f"[exit-inflection] {len(sessions)}세션 {sessions[0]}~{sessions[-1]} · IS/OOS {is_cut}")
    print(f"레짐 세션: 상승 {doc['regime_counts']['uptrend_sessions']} / 하락 {doc['regime_counts']['downtrend_sessions']}")
    for fam in families:
        print(f"\n=== {fam} · {len(families[fam])}건")
        print(f"{'rule':24s} {'n':>3s} {'EV%':>7s} {'win':>5s} {'PF':>5s} {'$':>7s} {'MDD$':>7s} {'t':>5s} {'hold':>4s} {'OOS':>7s} {'상승':>7s} {'하락':>7s}")
        for rule in rules:
            r = results[fam][rule]
            a = r["all"]
            if not a.get("n"):
                print(f"{rule:24s}  —")
                continue
            print(f"{rule:24s} {a['n']:3d} {a['ev_pct']:+7.3f} {a['win_pct']:5.1f} {(a['pf'] or 0):5.2f} "
                  f"{a['pnl_usd']:+7.2f} {a['mdd_usd']:7.2f} {a['t']:+5.2f} {a['avg_hold_min']:4.0f} "
                  f"{r['oos'].get('ev_pct', 0):+7.3f} {r['uptrend_sessions'].get('ev_pct', 0):+7.3f} "
                  f"{r['downtrend_sessions'].get('ev_pct', 0):+7.3f}")
    print("\n[EA 상태별] " + json.dumps({
        k: {"n": v["n"], "sell_now": v["sell_now_ev_pct"],
            "hold": v["rules"].get("hold_close", {}).get("ev_pct"),
            "chandelier09": v["rules"].get("chandelier09", {}).get("ev_pct"),
            "inflection": v["rules"].get("inflection_v1(2표)", {}).get("ev_pct")}
        for k, v in cond.items()}, ensure_ascii=False))
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
