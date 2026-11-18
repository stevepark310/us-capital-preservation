#!/usr/bin/env python3
"""Before/After A/B — C5 파일럿 + 구조 개선 3건의 실측 효과 (2026-11-18).

이 스크립트는 **프로덕션 함수를 그대로 호출**해서 판단한다(복사본 아님):
- 진입 신호: `core.entry_signals.dip_daily` / `dip_signal_from_metrics` / `bear_alpha_gate` /
  `c5_open30_signal`
- 사이징·브래킷: `core.autotrader.AutonomousTrader._size` / `_new_bracket` / `_sl_pct`
- 청산: `core.exit_rules.decide`
- 예산·게이트: `core.execution_policy`

그래서 **같은 스크립트를 ① HEAD(개선 전) 체크아웃과 ② 현재 워크트리에서 각각 돌리면**
Before/After가 나온다. 모듈 능력은 feature-detect로 구분한다(C5 함수·rvol_required·
price_structure 확인·bracket_specs 유무). 개선 전 코드에서 없는 것은 그 시점 규칙으로
대체한다(예: RVOL 필수, 2-of-2 게이트, C5 없음).

가정(연구 문서와 동일)
- 10분봉 판정, 체결 = 판정 봉 종가, 왕복 0.24%.
- 지표는 5분봉에서 계산(엔진이 10분마다 5분봉 지표를 읽는 순서와 동일).
- 신중 모드(2026-11-16 23:44 ~ 11-18 06:20) 강제청산 05:40 KST는 두 실행 모두 동일하게 적용.
- 실주문 없음. data/ 파일을 쓰지 않는다(읽기 전용).

실행
    python3 scripts/research/run_after_ab.py --label after --out data/research_after_ab_20261118.json
    (개선 전) git archive HEAD | tar -x -C .before && ln -s ../data_cache .before/data_cache \\
              && cd .before && python3 scripts/research/run_after_ab.py --label before --out ../data/research_before_ab_20261118.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import i10_lib as L                                    # noqa: E402
from core import entry_signals, execution_policy, exit_rules   # noqa: E402
from core.autotrader import AutonomousTrader           # noqa: E402

DATA = os.path.join(L.BASE_DIR, "data")

# ---- feature detect (개선 전/후 코드 공용)
try:
    from core import bracket_specs as BS
    HAS_SPECS = True
except Exception:                                       # pragma: no cover
    BS = None
    HAS_SPECS = False
HAS_C5 = hasattr(entry_signals, "c5_open30_signal")
HAS_ATR_HELPER = hasattr(entry_signals, "intraday_atr_pct")
HAS_SCORING = hasattr(entry_signals, "dip_signal_from_metrics")
HAS_STRUCT_GATE = "price_structure" in ((entry_signals.BEAR_GATE.get("confirmations") or {})
                                       if hasattr(entry_signals, "BEAR_GATE") else {})
HAS_VWAP_STATE = hasattr(entry_signals, "intraday_vwap_state")

OPEN_MIN = 9 * 60 + 30
BLACKOUT_END = 10 * 60          # 10:00 ET
NO_ENTRY_AFTER = 14 * 60 + 30
FORCE_EXIT = 15 * 60 + 40       # 15:40 ET = 05:40 KST
DIP_UNIVERSE = ["SOXL", "TQQQ"]
HEDGE_UNIVERSE = ["SOXS", "SQQQ"]
HEDGE_NOTIONAL_CAP = 60.0
MAX_ENTRIES = 3
MAX_OPEN = 4
COOLDOWN_MIN = 20.0
KST_SHIFT = 14 * 60             # 저장소 규약(23:30 KST = 09:30 ET)
RT_COST = L.RT_COST


def kst_clock(et_day: str, dec_min: int) -> Dict[str, Any]:
    total = (dec_min + KST_SHIFT) % (24 * 60)
    return {"now_kst": f"{et_day} {total // 60:02d}:{total % 60:02d}:00",
            "phase": "REGULAR", "session_date": et_day}


def hm_min(hm: str) -> int:
    h, m = str(hm).split(":")[:2]
    return int(h) * 60 + int(m)


def bars_5m(df5: pd.DataFrame, ts: pd.Timestamp) -> List[Dict[str, float]]:
    """판정 시점까지의 5분봉(최근 120개) — 엔진의 fetch_intraday_bars(period=2d)와 같은 형태."""
    if df5 is None or not len(df5):
        return []
    sub = df5[df5.index < ts]
    if not len(sub):
        return []
    sub = sub.tail(120)
    return [{"t": str(ix), "c": float(r["Close"]), "h": float(r["High"]), "l": float(r["Low"]),
             "v": float(r.get("Volume") or 0)} for ix, r in sub.iterrows()]


def _atr_pct_local(bars: List[Dict[str, float]]) -> Optional[float]:
    if not bars or len(bars) < 8:
        return None
    df = pd.DataFrame(bars)
    c = df["c"].astype(float)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - c.shift()).abs(), (df["l"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    return round(atr / float(c.iloc[-1]) * 100, 2)


def atr_pct_of(bars: List[Dict[str, float]]) -> Optional[float]:
    if HAS_ATR_HELPER:
        return entry_signals.intraday_atr_pct(bars)
    return _atr_pct_local(bars)


def vwap_state_local(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    if not bars:
        return None
    day = str(bars[-1]["t"])[:10]
    today = [b for b in bars if str(b["t"])[:10] == day]
    if len(today) < 6:
        return None
    num = sum(((b["h"] + b["l"] + b["c"]) / 3.0) * b["v"] for b in today)
    den = sum(b["v"] for b in today)
    px = today[-1]["c"]
    vwap = num / den if den > 0 else sum((b["h"] + b["l"] + b["c"]) / 3.0 for b in today) / len(today)
    return {"price": round(px, 2), "vwap": round(vwap, 2), "below_vwap": px < vwap, "bars": len(today)}


def vwap_state(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    if HAS_VWAP_STATE:
        return entry_signals.intraday_vwap_state(bars)
    return vwap_state_local(bars)


def dip_verdict(m: Optional[Dict[str, Any]], filters: Dict[str, Any]) -> Dict[str, Any]:
    """DIP 판정 — 개선 후 코드는 프로덕션 스코어러, 개선 전 코드는 당시 규칙(전부 필수)."""
    if HAS_SCORING:
        return entry_signals.dip_signal_from_metrics(m, filters)
    if not m:
        return {"ok": False, "why_not": "5분봉 부족", "score": 0.0}
    rsi2_max = float(filters.get("rsi2_max", 20.0))
    pctb_max = float(filters.get("pct_b_max", 0.15))
    rvol_min = float(filters.get("rvol_min", 1.0))
    ok = (m["rsi2"] <= rsi2_max and m["pct_b"] <= pctb_max and m["rvol"] is not None
          and m["rvol"] >= rvol_min)
    return {"ok": bool(ok), "score": 0.0,
            "why_not": None if ok else f"RSI2 {m['rsi2']} / %B {m['pct_b']} / RVOL {m['rvol']}"}


# ------------------------------------------------------------------ daily
def load_daily() -> Dict[str, pd.DataFrame]:
    path = os.path.join(L.CACHE, "__core_daily.pkl")
    try:
        import pickle
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return {}


def daily_upto(daily: Dict[str, pd.DataFrame], day: str) -> Dict[str, pd.DataFrame]:
    out = {}
    for sym, df in (daily or {}).items():
        sub = df[df.index < pd.Timestamp(day)]
        if len(sub):
            out[sym] = sub
    return out


def prev_close_of(daily: Dict[str, pd.DataFrame], sym: str, day: str) -> Optional[float]:
    df = (daily or {}).get(sym)
    if df is None:
        return None
    sub = df[df.index < pd.Timestamp(day)]
    if not len(sub):
        return None
    return float(sub["Close"].iloc[-1])


# ------------------------------------------------------------------ simulator
def simulate(days: List[str], panel: Dict[str, pd.DataFrame], panel5: Dict[str, pd.DataFrame],
             daily: Dict[str, pd.DataFrame], vix10: pd.Series, dxy10: pd.Series,
             sentiment: Optional[float], label: str,
             equity0: float = 883.5, cash0: float = 636.0,
             force_exit: bool = True, cautious_force: bool = True) -> Dict[str, Any]:
    policy = execution_policy.load_policy()
    g = execution_policy.guardrails(policy)
    trader = AutonomousTrader(policy=policy, dry_run=True)
    params = trader._strategy_params()
    filters = dict(params.get("entry_filters") or {})
    risk_scale = float(params.get("risk_scale", 1.0) or 1.0)
    c5_cfg = trader._c5_cfg() if HAS_C5 else {}
    reserve = float(g.get("cash_reserve_floor_usd", 150.0))

    cash, equity = float(cash0), float(equity0)
    brackets: Dict[str, Dict[str, Any]] = {}
    last_exit: Dict[str, pd.Timestamp] = {}
    cooldown: Dict[str, pd.Timestamp] = {}
    trades: List[Dict[str, Any]] = []
    blocks: Dict[str, int] = {}
    per_day: Dict[str, Dict[str, Any]] = {}
    gate_stats = {"cycles_vix_ge24": 0, "cycles_gate_armed": 0, "hedge_entries": 0,
                  "c5_candidates": 0, "c5_entries": 0, "c5_blackout_sessions": 0}
    entries_log: List[Dict[str, Any]] = []

    def block(reason: str) -> None:
        blocks[reason] = blocks.get(reason, 0) + 1

    def mark(sym: str, ts: pd.Timestamp) -> Optional[float]:
        b = panel.get(sym)
        if b is None or not len(b):
            return None
        sub = b[b.index < ts]
        if not len(sub):
            return None
        return float(sub["Close"].iloc[-1])

    for day in days:
        gday = panel["SOXL"][panel["SOXL"]["session"] == day]
        if not len(gday):
            continue
        day_rec = {"realized": 0.0, "entries": 0, "trades": 0, "halted": False}
        prev_close_soxl = prev_close_of(daily, "SOXL", day)
        for _hm, bar in gday.iterrows():
            ts = pd.Timestamp(_hm) + pd.Timedelta(minutes=10)
            dec = hm_min(bar["hm_end"])
            clock = kst_clock(day, dec)
            price_now = float(bar["Close"])

            # ---- 평가
            mtm = cash
            for tk, b in brackets.items():
                px = mark(tk, ts) or float(b.get("entry_price") or 0)
                mtm += px * exit_rules.remaining_qty(b)
            equity = mtm

            # ---- 1) 청산
            for tk in list(brackets.keys()):
                b = brackets[tk]
                if not exit_rules.is_open(b):
                    continue
                px = mark(tk, ts)
                if px is None:
                    continue
                b["highest_high"] = max(float(b.get("highest_high") or b.get("entry_price") or px), px)
                d = exit_rules.decide(b, px, clock)
                if (d is None and force_exit and cautious_force and dec >= FORCE_EXIT
                        and not b.get("force_exited") and not b.get("levels_locked")):
                    d = {"action": "SELL", "qty": exit_rules.remaining_qty(b), "close": True,
                         "reason": "신중 모드 강제청산 (05:40 KST)"}
                if not d:
                    continue
                qty = min(int(d.get("qty") or 0), exit_rules.remaining_qty(b))
                if qty <= 0:
                    continue
                entry = float(b.get("entry_price") or 0)
                fee = entry * qty * RT_COST
                cash += px * qty - fee
                pnl = (px - entry) * qty - fee
                day_rec["realized"] += pnl
                day_rec["trades"] += 1
                trades.append({"date": day, "ticker": tk, "qty": qty, "entry": round(entry, 2),
                               "exit": round(px, 2), "pnl_usd": round(pnl, 2),
                               "ret_pct": round(pnl / (entry * qty) * 100, 3) if entry else 0.0,
                               "notional_usd": round(entry * qty, 2), "fee_usd": round(fee, 2),
                               "source": b.get("entry_source"), "reason": d.get("reason"),
                               "entry_ts": b.get("filled_at_kst") or b.get("opened_at"),
                               "exit_ts": clock["now_kst"], "bracket_class": b.get("bracket_class")})
                b["closed"] = True
                b["force_exited"] = bool(b.get("force_exited"))
                cooldown[tk] = ts
                last_exit[tk] = ts
            if day_rec["realized"] <= -execution_policy.loss_limit_usd(equity, policy):
                day_rec["halted"] = True
            if day_rec["halted"]:
                continue

            # ---- 2) 진입 후보
            vix = None
            sub = vix10[vix10.index < ts]
            if len(sub):
                vix = float(sub.iloc[-1])
            dxy = None
            subd = dxy10[dxy10.index < ts]
            if len(subd):
                dxy = float(subd.iloc[-1])
            if vix is not None and vix >= 24.0:
                gate_stats["cycles_vix_ge24"] += 1
            vscale = execution_policy.vix_scale(vix, policy)
            if vscale <= 0:
                block("VIX>=28 리스크오프")
                continue

            cands: List[Dict[str, Any]] = []
            # (0) C5 파일럿 — 개장 30분 창
            if HAS_C5:
                win = c5_cfg.get("entry_window_min") or [30, 60]
                if int(win[0]) <= dec - OPEN_MIN <= int(win[1]):
                    done = any((e.get("source") == "C5_OPEN30_DIRECTION") for e in entries_log
                               if e["day"] == day)
                    open_c5 = any(b.get("entry_source") == "C5_OPEN30_DIRECTION"
                                  and exit_rules.is_open(b) for b in brackets.values())
                    if done or open_c5:
                        block("C5 1일 1회 이미 집행")
                    else:
                        bo = entry_signals.c5_blackout(day, c5_cfg)
                        if bo["blackout"]:
                            gate_stats["c5_blackout_sessions"] += 1
                            block(f"C5 블랙아웃({bo.get('reason')})")
                        elif prev_close_soxl:
                            sig = entry_signals.c5_open30_signal(prev_close_soxl, price_now, None,
                                                                 day, config=c5_cfg)
                            direction = sig.get("direction")
                            if not direction:
                                block(f"C5 방향 미확정({sig.get('reason')})")
                            else:
                                tick = None
                                for t in sig.get("tickers") or []:
                                    busy = t in brackets and exit_rules.is_open(brackets[t])
                                    hot = (t in cooldown
                                           and (ts - cooldown[t]).total_seconds() / 60.0 < COOLDOWN_MIN)
                                    if not busy and not hot:
                                        tick = t
                                        break
                                atr_t = atr_pct_of(bars_5m(panel5.get(tick or sig["tickers"][0]), ts)) \
                                    if sig.get("tickers") else None
                                sig2 = entry_signals.c5_open30_signal(prev_close_soxl, price_now, atr_t,
                                                                      day, config=c5_cfg)
                                if tick and sig2.get("active"):
                                    gate_stats["c5_candidates"] += 1
                                    cands.append({"source": "C5_OPEN30_DIRECTION", "ticker": tick,
                                                  "atr_pct": atr_t, "signal": sig2, "pilot": "C5",
                                                  "notional_cap": float(c5_cfg.get("budget_usd") or 150.0),
                                                  "time_exit_kst": sig2.get("exit_kst"),
                                                  "allow_opening_entry": True,
                                                  "why": f"C5 {direction}"})
            # 개장 유예 / 신규 진입 마감(비-C5)
            in_blackout = dec <= BLACKOUT_END
            if dec > NO_ENTRY_AFTER:
                block("신규 진입 마감(14:30 ET)")
                continue
            if day_rec["entries"] >= MAX_ENTRIES:
                block("세션 신규 진입 3건 한도")
                continue
            if len([b for b in brackets.values() if exit_rules.is_open(b)]) >= MAX_OPEN:
                block("최대 보유 4종목")
                continue

            # (1) DIP_SWING_DAILY — 세션 90분 창
            if not in_blackout and dec <= OPEN_MIN + 90:
                dsub = daily_upto(daily, day)
                scan = entry_signals.dip_daily(dsub, DIP_UNIVERSE, filters)
                for s in scan.get("signals") or []:
                    cands.append({"source": "DIP_SWING_DAILY", "ticker": s.get("symbol"),
                                  "atr_pct": s.get("atr_pct"), "signal": s,
                                  "why": f"일봉 RSI2 {s.get('rsi2')} %B {s.get('pct_b')}"})
            # (2) DIP_SWING_INTRADAY — 개장 30분 이후, 5분봉 지표
            if not in_blackout and dec - OPEN_MIN >= 30:
                scored = []
                for tk in DIP_UNIVERSE:
                    b5 = bars_5m(panel5.get(tk), ts)
                    m = entry_signals.intraday_dip_metrics(b5)
                    if not m:
                        continue
                    v = dip_verdict(m, filters)
                    if v.get("ok"):
                        scored.append((float(v.get("score") or 0.0), tk, m))
                scored.sort(reverse=True)
                for sc, tk, m in scored:
                    cands.append({"source": "DIP_SWING_INTRADAY", "ticker": tk,
                                  "atr_pct": m.get("atr_pct"), "signal": m,
                                  "why": f"5분봉 RSI2 {m.get('rsi2')} %B {m.get('pct_b')} 점수 {sc}"})
            # (3) BEAR_ALPHA — 1-of-1(개선 후) / 2-of-2(개선 전)
            if not in_blackout:
                snap = {"macro": {"vix": {"last": vix}, "dxy": {"last": dxy}}}
                if HAS_STRUCT_GATE:
                    st = vwap_state(bars_5m(panel5.get("SOXL"), ts))
                    gate = entry_signals.bear_alpha_gate(snap, sentiment, price_structure=st)
                else:
                    gate = entry_signals.bear_alpha_gate(snap, sentiment)
                if gate.get("armed"):
                    gate_stats["cycles_gate_armed"] += 1
                    for tk in HEDGE_UNIVERSE:
                        cands.append({"source": "BEAR_ALPHA_HEDGE", "ticker": tk,
                                      "atr_pct": atr_pct_of(bars_5m(panel5.get(tk), ts)),
                                      "signal": gate, "notional_cap": HEDGE_NOTIONAL_CAP,
                                      "why": f"VIX {vix}"})

            # ---- 3) 집행
            c5_open = next((tk for tk, b in brackets.items()
                            if b.get("entry_source") == "C5_OPEN30_DIRECTION" and exit_rules.is_open(b)), None)
            for c in cands:
                tk = c["ticker"]
                src = c["source"]
                if src != "C5_OPEN30_DIRECTION" and in_blackout:
                    block("개장 유예(09:30~10:00 ET)")
                    continue
                if tk in brackets and exit_rules.is_open(brackets[tk]):
                    block(f"{tk} 이미 보유(물타기 금지)")
                    continue
                if tk in cooldown:
                    age = (ts - cooldown[tk]).total_seconds() / 60.0
                    if age < COOLDOWN_MIN:
                        block(f"{tk} 쿨다운 {COOLDOWN_MIN:.0f}분")
                        continue
                conflict = trader._c5_conflict(src, tk, c5_open) if HAS_C5 else None
                if conflict:
                    block("C5 방향 충돌")
                    continue
                px = mark(tk, ts)
                if not px or px < float(g.get("min_price_usd", 3.0)):
                    block(f"{tk} 가격 확인 불가")
                    continue
                eb = execution_policy.entry_budget(equity, cash, policy, vscale * risk_scale)
                budget = float(eb["budget_usd"])
                if c.get("notional_cap"):
                    budget = min(budget, float(c["notional_cap"]))
                planned = 0.0
                qty, sizing = trader._size(px, budget, equity, max(0.0, cash - planned), g, c, params)
                if qty <= 0:
                    block(f"{tk} 수량 0 (예산 ${budget:.2f} < 1주 ${px:.2f})")
                    continue
                fill = px
                b = trader._new_bracket(c, tk, fill, qty, None, src, params)
                b["bought_qty"] = qty
                b["pending_entry"] = False
                b["filled_at_kst"] = clock["now_kst"]
                brackets[tk] = b
                cash -= fill * qty
                day_rec["entries"] += 1
                if src == "BEAR_ALPHA_HEDGE":
                    gate_stats["hedge_entries"] += 1
                if src == "C5_OPEN30_DIRECTION":
                    gate_stats["c5_entries"] += 1
                    c5_open = tk
                entries_log.append({"day": day, "ts": clock["now_kst"], "ticker": tk, "qty": qty,
                                    "price": round(fill, 2), "source": src,
                                    "notional_usd": round(fill * qty, 2),
                                    "bracket_class": b.get("bracket_class"),
                                    "sl_pct": b.get("initial_sl_pct"),
                                    "trail_atr_mult": b.get("trail_atr_mult"),
                                    "force_exit_kst": b.get("force_exit_kst"),
                                    "budget_usd": round(budget, 2)})
        # 세션 종료 — 남은 포지션은 다음 세션으로(스윙만)
        per_day[day] = {"realized_usd": round(day_rec["realized"], 2), "entries": day_rec["entries"],
                        "trades": day_rec["trades"], "halted": day_rec["halted"],
                        "open_at_close": [tk for tk, b in brackets.items() if exit_rules.is_open(b)]}
    exec_trades = [t for t in trades]
    stats = L.trade_stats(exec_trades, 150.0)
    return {"label": label, "stats": stats, "per_day": per_day, "blocks": blocks,
            "gate_stats": gate_stats, "entries": entries_log,
            "final_cash": round(cash, 2), "final_equity": round(equity, 2),
            "params": {k: params.get(k) for k in ("trail_atr_mult", "initial_sl_atr_mult",
                                                  "max_hold_sessions", "risk_scale")},
            "entry_filters": filters,
            "capabilities": {"c5": HAS_C5, "rvol_bonus": HAS_SCORING, "struct_gate": HAS_STRUCT_GATE,
                             "bracket_specs": HAS_SPECS},
            "trades": exec_trades}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="after")
    ap.add_argument("--out", default=os.path.join(DATA, "research_after_ab_20261118.json"))
    args = ap.parse_args(argv)

    panel = L.panel(["SOXL", "TQQQ", "SOXS", "SQQQ", "^VIX", "QQQ"])
    panel5 = {s: L.regular_session(L.load5m(s, "60d")) for s in ["SOXL", "TQQQ", "SOXS", "SQQQ"]}
    daily = load_daily()
    vix10 = L.to10m(L.load5m("^VIX", "60d"))["Close"]
    dxy10 = L.to10m(L.load5m("DX-Y.NYB", "60d"))["Close"]
    sentiment = None
    try:
        sentiment = float(json.load(open(os.path.join(DATA, "news_sentiment_report.json"),
                                         encoding="utf-8")).get("average_sentiment_score"))
    except Exception:
        sentiment = None

    all_days = sorted(panel["SOXL"]["session"].unique())
    windows = {"5d": [d for d in all_days if d >= "2026-11-11"],
               "60d": all_days}
    out: Dict[str, Any] = {"label": args.label, "window_days": {k: len(v) for k, v in windows.items()},
                           "sentiment_assumed": sentiment, "cost_round_trip_pct": RT_COST * 100}
    for name, days in windows.items():
        r = simulate(days, panel, panel5, daily, vix10, dxy10, sentiment, args.label)
        out[name] = r
        st = r["stats"]
        print(f"[{args.label}/{name}] trades {st['n']} pnl ${st['pnl_usd']:+.2f} "
              f"win {st['win_pct']}% pf {st['pf']} mdd ${st['mdd_usd']} fee ${st['fee_usd']} "
              f"entries {len(r['entries'])} c5 {r['gate_stats']['c5_entries']} "
              f"hedge {r['gate_stats']['hedge_entries']} gate_armed {r['gate_stats']['cycles_gate_armed']}")
        print(f"   per_day: {json.dumps({k: v['realized_usd'] for k, v in r['per_day'].items()}, ensure_ascii=False)}")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
