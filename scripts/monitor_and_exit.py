#!/usr/bin/env python3
"""
Intraday Bracket Monitor & Auto-Execution (fill-anchored)

Changes from the previous version
--------------------------------
1. Quotes come from the shared snapshot (5m bars) instead of a fresh 1m download
   per ticker, and the monitor refuses to act outside the regular session.
2. Brackets are re-anchored ONCE per position from the realised fill: TP1/TP2/SL
   are recomputed with core/exit_planner from the actual entry price and the
   symbol's ATR, instead of trusting levels derived from the planned price.
3. The runner is no longer a fixed level: after TP2 it trails on
   max(chandelier(highest high - 0.9×ATR), EMA20), floored at +1.5% locked
   profit — so a runaway move is not cut at TP2 and a collapse still exits.
4. Orders are only sent while the US regular session is open.

Run:  python3 scripts/monitor_and_exit.py [--dry-run]
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import market_clock, market_snapshot, technical, ledger as ledger_mod
from core.exit_planner import AdaptiveExitPlanner

BRACKETS_FILE = os.path.join(BASE_DIR, "data", "active_brackets.json")
ORDERS_LOG = os.path.join(BASE_DIR, "data", "live_order_responses.jsonl")
PLANNER = AdaptiveExitPlanner()
DRY_RUN = "--dry-run" in sys.argv or os.getenv("CUBE_DRY_RUN") == "1"


def load_brackets() -> Dict[str, Any]:
    try:
        with open(BRACKETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_brackets(b: Dict[str, Any]) -> None:
    with open(BRACKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)


def reanchor(bracket: Dict[str, Any], ticker: str, atr_pct: float, sentiment: float) -> Dict[str, Any]:
    """Recompute levels from the realised fill; record the provenance."""
    if bracket.get("bracket_version") == "fill_anchored_v2":
        return bracket
    qty = int(bracket.get("bought_qty", 1) or 1)
    hard_floor = float(bracket["sl"]) if ticker == "NVDA" else None
    plan = PLANNER.plan(
        symbol=ticker,
        planned_price=bracket.get("planned_price", bracket.get("entry_price")),
        fill_price=bracket.get("entry_price"),
        qty=qty, atr_pct=atr_pct, sentiment_score=sentiment,
        hard_floor_price=hard_floor, allow_runner=int(bracket.get("runner_qty", 0) or 0) > 0,
    )
    before = {k: bracket.get(k) for k in ("tp1", "tp2", "sl")}
    bracket["recomputed"] = {"tp1": plan.tp1_price, "tp2": plan.tp2_price, "sl": plan.hard_sl_price,
                             "atr_pct": round(atr_pct, 2)}
    if bracket.get("levels_locked"):
        # user-specified exits stay exactly as given; the recomputed values are informational only
        bracket["bracket_version"] = "fill_anchored_locked"
        bracket["recalc_note"] = (f"사용자 지정 레벨 유지 (TP1 {bracket.get('tp1')} / SL {bracket.get('sl')}). "
                                  f"참고 재산정: TP1 {plan.tp1_price} / TP2 {plan.tp2_price} / SL {plan.hard_sl_price}")
        bracket.setdefault("highest_high", bracket.get("entry_price"))
        return bracket
    if not bracket.get("tp1_filled"):
        bracket["tp1"] = plan.tp1_price
    bracket["tp2"] = plan.tp2_price
    bracket["sl"] = plan.hard_sl_price
    bracket["breakeven_stop"] = plan.breakeven_price
    bracket["fill_price"] = plan.fill_price
    bracket["slippage_bps"] = plan.slippage_bps
    bracket["atr_pct_at_plan"] = round(atr_pct, 2)
    bracket["runner_trail"] = {"mode": plan.runner_mode, "atr_mult": PLANNER.params["trail_atr_mult"],
                               "locked_floor": round(plan.fill_price * 1.015, 2)}
    bracket["bracket_version"] = "fill_anchored_v2"
    bracket["recalc_note"] = f"{before} → TP1 {bracket.get('tp1')} / TP2 {bracket['tp2']} / SL {bracket['sl']}"
    bracket.setdefault("highest_high", bracket.get("entry_price"))
    return bracket


def place_sell(broker, ticker: str, qty: int, price: float, reason: str, dry: bool) -> Dict[str, Any]:
    record = {
        "ts": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S KST"),
        "side": "SELL", "ticker": ticker, "qty": qty, "price": round(float(price), 2), "reason": reason,
    }
    if dry or broker is None:
        record["status"] = "DRY_RUN"
        print(f"[DRY RUN] SELL {ticker} {qty} @ ${price:.2f} ({reason})")
        with open(ORDERS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record
    payload = {
        "act_no": broker.act_no, "fc_sec_trd_nat_cd": "200", "iem_cd": ticker,
        "orr_qty": qty, "fc_orr_uit_pr": round(float(price), 2),
        "ahi_nmn_pr_tp_cd": "00", "wtm_cur_knd_cd": "1",
    }
    try:
        res = broker._call_api("/gbstock/order/v1/sell", payload)
        record["response"] = res
        record["status"] = "SUBMITTED" if res.get("rsp_cd") in ("00171", "00166", "00000") else "REJECTED"
    except Exception as e:
        record["status"] = "ERROR"
        record["error"] = str(e)
    with open(ORDERS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[SELL] {ticker} {qty} @ ${price:.2f} ({reason}) → {record['status']}")
    return record


def sentiment_scores() -> Dict[str, float]:
    """Same source the orchestrator uses, so both paths re-anchor identically."""
    try:
        with open(os.path.join(BASE_DIR, "data", "news_sentiment_report.json"), "r", encoding="utf-8") as f:
            rep = json.load(f)
        return {k: float(v.get("sentiment_score", 50.0)) for k, v in (rep.get("ticker_reports") or {}).items()}
    except Exception:
        return {}


def run_check() -> List[Dict[str, Any]]:
    clock = market_clock.resolve()
    senti = sentiment_scores()
    snap = market_snapshot.build_snapshot(quiet=True)
    quotes = snap.get("quotes", {})
    hist = market_snapshot.fetch_history()
    techs = {s: technical.analyze(s, hist[s], quotes.get(s)) for s in hist if len(hist[s]) > 30}
    brackets = load_brackets()
    actions: List[Dict[str, Any]] = []

    if clock["phase"] != "REGULAR":
        print(f"[monitor] {clock['now_kst']} phase={clock['phase']} — 주문 없이 브래킷 상태만 재산정합니다.")
        for t, b in brackets.items():
            if isinstance(b, dict) and b.get("entry_price") and not (
                    b.get("carry_parking") or b.get("note") == "CARRY_PARKING" or b.get("closed")):
                atr = (techs.get(t) or {}).get("atr_pct") or 6.0
                reanchor(b, t, atr, senti.get(t, 50.0))
        save_brackets(brackets)
        return actions

    broker = None
    if not DRY_RUN:
        try:
            from execution.nh_trader import NHBroker
            broker = NHBroker(dry_run=False)
        except Exception as e:
            print(f"[monitor] broker unavailable ({e}) — DRY RUN으로 전환")
            DRY_RUN = True

    for t, b in brackets.items():
        if not isinstance(b, dict) or "entry_price" not in b:
            continue
        qty_total = int(b.get("bought_qty", 0) or 0)
        if qty_total <= 0:
            continue
        if b.get("carry_parking") or b.get("note") == "CARRY_PARKING" or b.get("closed"):
            continue
        atr_pct = (techs.get(t) or {}).get("atr_pct") or 6.0
        b = reanchor(b, t, atr_pct, senti.get(t, 50.0))
        price = (quotes.get(t) or {}).get("intraday_price") or (quotes.get(t) or {}).get("last")
        if not price:
            continue
        b["highest_high"] = max(float(b.get("highest_high") or b["entry_price"]), float(price))

        remaining = qty_total - (int(b.get("tp1_qty", 0)) if b.get("tp1_filled") else 0) \
                              - (int(b.get("tp2_qty", 0)) if b.get("tp2_filled") else 0)

        # runner trail after TP2
        if b.get("tp2_filled") and remaining > 0 and b.get("runner_trail"):
            atr_val = float(b.get("entry_price")) * atr_pct / 100.0
            ema20 = (techs.get(t) or {}).get("ema20") or 0.0
            rs = PLANNER.runner_stop(float(b["entry_price"]), b["highest_high"], atr_val, ema20,
                                     current_price=float(price))
            if rs["stop"] > float(b.get("sl", 0)):
                b["sl"] = rs["stop"]
                b["runner_trail_source"] = rs["source"]

        if float(price) <= float(b["sl"]) and not b.get("sl_triggered"):
            if remaining > 0:
                actions.append(place_sell(broker, t, remaining, price,
                                          f"STOP/TRAIL (${price:.2f} ≤ ${b['sl']:.2f}, {b.get('runner_trail_source','stop')})", DRY_RUN))
                b["sl_triggered"] = True
        elif not b.get("tp1_filled") and float(price) >= float(b["tp1"]):
            actions.append(place_sell(broker, t, int(b.get("tp1_qty", 1)), price,
                                      f"TP1 (+{((price/float(b['entry_price']))-1)*100:.1f}%)", DRY_RUN))
            b["tp1_filled"] = True
            b["sl"] = max(float(b["sl"]), float(b.get("breakeven_stop") or b["entry_price"]))
        elif b.get("tp1_filled") and b.get("tp2") and not b.get("tp2_filled") and float(price) >= float(b["tp2"]):
            actions.append(place_sell(broker, t, int(b.get("tp2_qty", 1)), price,
                                      f"TP2 (+{((price/float(b['entry_price']))-1)*100:.1f}%)", DRY_RUN))
            b["tp2_filled"] = True
            b["sl"] = max(float(b["sl"]), float(b["entry_price"]) * 1.015)
        brackets[t] = b

    save_brackets(brackets)
    return actions


if __name__ == "__main__":
    acts = run_check()
    print(f"Total actions: {len(acts)} (dry_run={DRY_RUN})")
