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
5. 2026-11-17: 무인 집행 엔진(`core/autotrader.py`)과 갈라지지 않도록 청산 판정을
   `core/exit_rules`로 일원화하고, `--execute` 옵션은 `execution/order_bridge`를 통해
   GitHub Actions로 주문한다(로컬 NH API 8443 차단 대응). 단일 작성자 리스로
   두 엔진이 동시에 같은 브래킷을 팔지 않는다.

Run:  python3 scripts/monitor_and_exit.py [--dry-run] [--execute]
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import market_clock, market_snapshot, technical, ledger as ledger_mod
from core import exit_rules
from core.session_time import kst_hhmm, session_minutes as _session_minutes, time_reached
from core.exit_planner import AdaptiveExitPlanner

BRACKETS_FILE = os.path.join(BASE_DIR, "data", "active_brackets.json")
ORDERS_LOG = os.path.join(BASE_DIR, "data", "live_order_responses.jsonl")
LEASE_FILE = os.path.join(BASE_DIR, "data", "autotrader_lease.json")
PLANNER = AdaptiveExitPlanner()
DRY_RUN = "--dry-run" in sys.argv or os.getenv("CUBE_DRY_RUN") == "1"
EXECUTE = "--execute" in sys.argv and not DRY_RUN
ROUTER = None


def _lease_read() -> Dict[str, Any]:
    try:
        with open(LEASE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _lease_write(owner: Optional[str]) -> None:
    payload = {"owner": owner}
    if owner:
        payload.update({"heartbeat_kst": (datetime.now(timezone.utc).replace(tzinfo=None)
                                          + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
                        "pid": os.getpid(), "mode": "monitor_execute"})
    else:
        payload["released_kst"] = (datetime.now(timezone.utc).replace(tzinfo=None)
                                   + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
    with open(LEASE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _lease_holder() -> Optional[str]:
    """다른 엔진이 최근 15분 내 하트비트를 남겼으면 그 owner를 돌려준다."""
    lease = _lease_read()
    owner = lease.get("owner")
    if not owner:
        return None
    try:
        ts = datetime.strptime(str(lease.get("heartbeat_kst"))[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)
    return owner if (now - ts) < timedelta(minutes=15) else None


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
    if bracket.get("bracket_version") in ("fill_anchored_v2", "swing_validated", "chandelier_v2"):
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
    if bracket.get("swing_hold"):
        # DIP_SWING (사용자 승인 2026-11-14 21:29): the validated ATR×1.5 bracket and the
        # 3-session hold are the approved terms. Intraday re-anchoring would tighten the
        # stop and cut the position on day one, so the validated levels stay as registered.
        bracket["bracket_version"] = "swing_validated"
        bracket["recalc_note"] = (f"DIP_SWING 검증 레벨 유지 (TP {bracket.get('tp')} / SL {bracket.get('sl')}, "
                                  f"최대 {bracket.get('max_hold_sessions', 3)}세션). "
                                  f"참고 재산정: TP1 {plan.tp1_price} / TP2 {plan.tp2_price} / SL {plan.hard_sl_price}")
        bracket.setdefault("highest_high", bracket.get("entry_price"))
        return bracket
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


def chandelier_v2_exit(bracket: Dict[str, Any], price: float, clock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """v2 청산 — 샹들리에 트레일(0.9ATR) + 초기손절 0.75ATR + 3세션 시간청산.

    근거(2026-11-17 A/B, 2년 60분봉 39~51건): 고정 사다리(v1)는 SOXL ATR 27%에서
    손절이 2.5%로 클램프돼 39건 중 31건이 손절(-1.31%/건)이었다. 트레일 v2는
    SOXL +2.48%/건, TQQQ +0.70%/건으로 두 종목 모두 개선됐다.
    부분익절 없이 전량 1회 청산한다(계좌 1~2주 단위에서 분할은 수수료만 늘린다).
    """
    session = clock.get("session_date")
    if bracket.get("last_session_date") and bracket["last_session_date"] != session:
        bracket["sessions_held"] = int(bracket.get("sessions_held", 1) or 1) + 1
    bracket["last_session_date"] = session
    bracket.setdefault("sessions_held", 1)
    entry = float(bracket.get("entry_price") or 0)
    if entry <= 0:
        return None
    atr_pct = float(bracket.get("atr_pct_at_plan") or (bracket.get("ladder_params") or {}).get("atr_pct") or 6.0)
    atr_abs = entry * atr_pct / 100.0
    k = float(bracket.get("trail_atr_mult", 0.9))
    hh = max(float(bracket.get("highest_high") or entry), float(price))
    bracket["highest_high"] = hh
    trail = hh - k * atr_abs
    if hh >= entry + float(bracket.get("be_after_atr", 1.0)) * atr_abs:
        trail = max(trail, entry * (1 + float(bracket.get("breakeven_pct", 0.34)) / 100))
    bracket["trail_stop"] = round(trail, 2)
    max_hold = int(bracket.get("max_hold_sessions", 3) or 3)
    late = str(clock.get("now_kst", ""))[11:16] >= "05:45"

    if not time_reached(clock, bracket.get("sl_active_after_kst")):
        return None                       # 개장 15분 유예
    if float(price) <= float(bracket.get("sl", 0)):
        return {"reason": f"v2 STOP (${price:.2f} ≤ ${bracket['sl']:.2f})", "close": True, "stop": True}
    if float(price) <= trail:
        return {"reason": f"v2 TRAIL (${price:.2f} ≤ ${trail:.2f}, 최고 ${hh:.2f} − {k}×ATR)",
                "close": True, "stop": False}
    if int(bracket.get("sessions_held", 1)) >= max_hold and late:
        return {"reason": f"v2 TIME EXIT ({max_hold}세션 종가 청산)", "close": True, "stop": False}
    return None


def chandelier_v2_exit(bracket: Dict[str, Any], price: float, clock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """v2 청산 — 판정 로직은 core/exit_rules 하나만 쓴다(무인 트레이더와 동일)."""
    decision = exit_rules.chandelier_v2_exit(bracket, price, clock)
    if not decision:
        return None
    return {"reason": decision["reason"], "close": decision.get("close", True),
            "stop": decision.get("stop", False)}


def place_sell(broker, ticker: str, qty: int, price: float, reason: str, dry: bool) -> Dict[str, Any]:
    record = {
        "ts": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S KST"),
        "side": "SELL", "ticker": ticker, "qty": qty, "price": round(float(price), 2), "reason": reason,
        "engine": "monitor_and_exit",
    }
    if dry or broker is None:
        record["status"] = "DRY_RUN"
        print(f"[DRY RUN] SELL {ticker} {qty} @ ${price:.2f} ({reason})")
        with open(ORDERS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    if EXECUTE:
        # 로컬 NH 8443이 막혀 있으므로 GitHub Actions 디스패치로 주문하고 접수 결과를 기록한다.
        res = ROUTER.place(ticker, "SELL", int(qty), round(float(price) * 0.997, 2), reason,
                           dry_run=False, extra={"engine": "monitor_and_exit"})
        record.update({"status": res.get("status"), "order_no": res.get("orr_no"),
                       "run_id": res.get("run_id"), "channel": res.get("channel"),
                       "limit_price": round(float(price) * 0.997, 2)})
        if res.get("error"):
            record["error"] = res["error"]
        print(f"[SELL/BRIDGE] {ticker} {qty} @ ~${price:.2f} ({reason}) → {record['status']}")
        return record

    act_no = broker._acct() if hasattr(broker, "_acct") else (broker.act_no or "").replace("-", "")
    payload = {
        "act_no": act_no, "fc_sec_trd_nat_cd": "200", "iem_cd": ticker,
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


def swing_exit_decision(bracket: Dict[str, Any], price: float, clock: Dict[str, Any]) -> Dict[str, Any]:
    """DIP_SWING 청산 판정 — core/exit_rules에 위임(엔진 간 규칙 불일치 방지)."""
    decision = exit_rules.swing_exit_decision(bracket, price, clock)
    if not decision:
        return None
    return {"reason": decision["reason"], "close": decision.get("close", False),
            "stop": decision.get("stop", False)}


def _autotrader_pending() -> set:
    """무인 엔진이 이미 주문을 내고 체결 확인을 기다리는 종목 — 중복 주문 방지."""
    try:
        with open(os.path.join(BASE_DIR, "data", "autotrader_state.json"), "r", encoding="utf-8") as f:
            st = json.load(f)
        return {p.get("ticker") for p in (st.get("pending") or []) if p.get("ticker")}
    except Exception:
        return set()


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

    dry_run = not EXECUTE
    broker = None
    global ROUTER
    busy_tickers = _autotrader_pending() if EXECUTE else set()
    lease_owner = "monitor_and_exit"
    if EXECUTE:
        holder = _lease_holder()
        if holder and holder != lease_owner:
            print(f"[monitor] 다른 집행 엔진이 실행 중(lease={holder}) — 이번 주기는 주문 없이 건너뜁니다.")
            return actions
        from execution.order_bridge import OrderRouter
        ROUTER = OrderRouter(dry_run=False)
        _lease_write(lease_owner)
    if not dry_run and not EXECUTE:
        try:
            from execution.nh_trader import NHBroker
            broker = NHBroker(dry_run=False)
        except Exception as e:
            print(f"[monitor] broker unavailable ({e}) — DRY RUN으로 전환")
            dry_run = True

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
        if t in busy_tickers:
            print(f"[monitor] {t}: 무인 엔진이 이미 주문을 내고 체결 확인 대기 중 — 이번 주기 청산 판단을 건너뜁니다.")
            continue
        b["highest_high"] = max(float(b.get("highest_high") or b["entry_price"]), float(price))

        remaining = qty_total - (int(b.get("tp1_qty", 0)) if b.get("tp1_filled") else 0) \
                              - (int(b.get("tp2_qty", 0)) if b.get("tp2_filled") else 0)

        # ---- DIP_SWING overnight hold: approved terms, whole-position exit, 3-session cap
        if b.get("swing_hold"):
            decision = swing_exit_decision(b, price, clock)
            if decision and remaining > 0 and not (decision["stop"] and b.get("sl_triggered")):
                actions.append(place_sell(broker, t, remaining, price, decision["reason"], dry_run))
                if decision["stop"]:
                    b["sl_triggered"] = True
                if decision["close"]:
                    b["closed"] = True
            brackets[t] = b
            continue

        # ---- v2 샹들리에 트레일 (2026-11-17 A/B 검증 — 신규 진입 기본 청산 규칙)
        if b.get("exit_mode") == "chandelier_v2":
            decision = chandelier_v2_exit(b, float(price), clock)
            if decision and remaining > 0 and not (decision["stop"] and b.get("sl_triggered")):
                actions.append(place_sell(broker, t, remaining, price, decision["reason"], dry_run))
                b["sl_triggered"] = bool(decision["stop"])
                b["closed"] = True
            brackets[t] = b
            continue

        # runner trail after TP2
        if b.get("tp2_filled") and remaining > 0 and b.get("runner_trail"):
            atr_val = float(b.get("entry_price")) * atr_pct / 100.0
            ema20 = (techs.get(t) or {}).get("ema20") or 0.0
            rs = PLANNER.runner_stop(float(b["entry_price"]), b["highest_high"], atr_val, ema20,
                                     current_price=float(price))
            if rs["stop"] > float(b.get("sl", 0)):
                b["sl"] = rs["stop"]
                b["runner_trail_source"] = rs["source"]

        # ---- 평단 사다리 / 실적 가드 (판정은 core/exit_rules — 무인 트레이더와 동일 규칙)
        decision = exit_rules.ladder_exit_decision(b, float(price), clock)
        if decision and remaining > 0:
            qty = min(int(decision.get("qty") or remaining), remaining)
            actions.append(place_sell(broker, t, qty, price, decision["reason"], dry_run))
            exit_rules.apply_post(b, decision)
        brackets[t] = b

    if EXECUTE:
        _lease_write(None)
    save_brackets(brackets)
    return actions


if __name__ == "__main__":
    acts = run_check()
    mode = "EXECUTE(bridge)" if EXECUTE else "DRY_RUN"
    print(f"Total actions: {len(acts)} (mode={mode})")
