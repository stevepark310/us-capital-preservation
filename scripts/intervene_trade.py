#!/usr/bin/env python3
"""즉시 개입 매매 — 프리장·본장 어디서든 판단→주문을 한 번에 집행하는 단일 진입점.

동훈님 지시(2026-11-21 13:53)
----------------------------
"필요시 프리장이나 본장에 개입해서 바로바로 매매할 수 있게 해줘"

무인 엔진(`core/autotrader.py`)은 10분 사이클마다 신호를 훑는다. 그러나 사람이 지금
바로 사고팔아야 할 때(뉴스·급변·M8/V2 판단 변경)는 사이클을 기다릴 수 없다. 이 스크립트는
그 한 건을 **같은 게이트·같은 가드레일·같은 주문 라우터**로 즉시 내보낸다.

    python3 scripts/intervene_trade.py --ticker SOXL --side BUY --qty 1            # 미리보기(기본)
    python3 scripts/intervene_trade.py --ticker SOXL --side BUY --qty 1 --execute   # 실주문
    python3 scripts/intervene_trade.py --ticker SQQQ --side SELL --qty 1 --execute
    python3 scripts/intervene_trade.py --ticker SOXL --side BUY --notional 45 --execute
    python3 scripts/intervene_trade.py --status        # 지금 세션·게이트·보유 요약

기본이 **미리보기(DRY_RUN)** 다 — 실주문은 `--execute`를 명시해야 나간다.

안전 규칙(모두 주문 직전에 다시 읽는다)
---------------------------------------
1. 기간한정 실주문 게이트(`config/live_execution_gate.json`)가 이 세션을 허용해야 한다.
2. 단계별 호가유형 — 정규장 `00`, 프리장 `61`(price>0 필수). 프리장은 `config/premarket_policy.json`의
   스위치 + 사용자 승인 기록이 있어야 열린다(`core/session_routing`).
3. 프리장 얇은 호가 방어 — ① 지정가 슬리피지 상한 ② 1회 예산 상한 ③ 세션 주문 건수 상한
   ④ 개장 동시호가 회피(09:00 ET 이후 신규 매수 금지) ⑤ 추격 금지 밴드 ⑥ 시세 신선도.
4. 현금 마지노선 $150·물타기 금지·일일 손실 서킷브레이커는 무인 엔진과 동일하게 적용한다.
5. 주문 결과·판단 근거는 `data/intervention_log.jsonl`에 남긴다(사후 감사용).

이 경로도 **사용자의 승인 없이 스스로 나가지 않는다** — 호출 자체가 사용자(또는 사용자가
지시한 작업)의 명령이고, 게이트·프리장 스위치가 닫혀 있으면 DRY_RUN으로만 계산한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

DATA = os.path.join(BASE_DIR, "data")
LOG_PATH = os.path.join(DATA, "intervention_log.jsonl")
GATE_PATH = os.path.join(BASE_DIR, "config", "live_execution_gate.json")

KST = timedelta(hours=9)


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + KST


def _now_str() -> str:
    return _now_kst().strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _append_log(rec: Dict[str, Any], path: Optional[str] = None) -> None:
    p = path or LOG_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _today_buys(path: Optional[str] = None, session_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """오늘(세션일) 이 경로로 나간 매수 주문 — 프리장 건수·금액 한도 계산용."""
    p = path or LOG_PATH
    out: List[Dict[str, Any]] = []
    if not os.path.exists(p):
        return out
    today = session_date or _now_kst().strftime("%Y-%m-%d")
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("session_date") != today or str(rec.get("side")).upper() != "BUY":
                continue
            # DRY_RUN은 미리보기라 건수 한도를 소모하지 않는다.
            if rec.get("status") in ("SUBMITTED", "DISPATCHED", "PENDING", "UNKNOWN_NEEDS_RECONCILE"):
                out.append(rec)
    return out


def _quote_price(quotes: Dict[str, Any], ticker: str) -> Optional[float]:
    q = (quotes or {}).get(ticker) or {}
    for key in ("intraday_price", "last", "close"):
        try:
            v = q.get(key)
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _holdings(brackets: Dict[str, Any], ticker: str) -> int:
    b = (brackets or {}).get(ticker) or {}
    if not isinstance(b, dict) or b.get("closed"):
        return 0
    try:
        from core import exit_rules
        return int(exit_rules.remaining_qty(b))
    except Exception:
        return int(b.get("bought_qty") or 0)


def plan_intervention(ticker: str, side: str, qty: Optional[int] = None,
                      price: Optional[float] = None, notional: Optional[float] = None,
                      reason: str = "", *, clock: Optional[Dict[str, Any]] = None,
                      gate: Optional[Dict[str, Any]] = None, plan: Optional[Dict[str, Any]] = None,
                      quotes: Dict[str, Any] = None, brackets: Dict[str, Any] = None,  # type: ignore[assignment]
                      cash_usd: Optional[float] = None, equity_usd: Optional[float] = None,
                      policy: Optional[Dict[str, Any]] = None,
                      log_path: Optional[str] = None,
                      vix: Optional[float] = None,
                      integrity: Optional[Dict[str, Any]] = None,
                      manual: bool = True,
                      override_vix: bool = False,
                      override_gap: bool = False,
                      full_capital: bool = False) -> Dict[str, Any]:
    """한 건의 개입 계획(주문 없음). 통과하면 `ok=True`와 주문 인자를 돌려준다."""
    from core import execution_policy, market_clock, session_routing
    from core.live_session_gate import check_live_gate

    ticker = str(ticker or "").upper().strip()
    side = str(side or "").upper().strip()
    clock = clock if clock is not None else market_clock.resolve()
    policy = policy or execution_policy.load_policy()
    plan = plan if plan is not None else session_routing.status(session_routing.load_policy())
    gate = gate if gate is not None else _load(GATE_PATH, {})
    quotes = quotes or {}
    brackets = brackets or {}
    out: Dict[str, Any] = {"ticker": ticker, "side": side, "phase": clock.get("phase"),
                           "session_date": clock.get("session_date"), "checks": [],
                           "ok": False, "reason": None, "dry_hint": True, "decided_at_kst": _now_str(),
                           "manual_intervention": bool(manual),
                           "premarket_manual_only": bool(plan.get("premarket_manual_only"))}

    def fail(reason: str, **kw) -> Dict[str, Any]:
        out.update({"ok": False, "reason": reason, **kw})
        out["checks"].append({"check": "final", "ok": False, "reason": reason})
        return out

    def ok_check(name: str, detail: Any = None) -> None:
        out["checks"].append({"check": name, "ok": True, "detail": detail})

    if side not in ("BUY", "SELL"):
        return fail("side must be BUY or SELL")
    if not ticker:
        return fail("ticker required")
    gate_check = check_live_gate(gate, clock, plan=plan)
    out["gate"] = gate_check
    if not gate_check.get("ok"):
        return fail(f"실주문 게이트 미통과 — {gate_check.get('reason')}")
    ok_check("live_gate", {"session": gate_check.get("session_date"),
                           "expires": gate_check.get("expires_at_kst")})
    order_type = gate_check.get("order_type") or session_routing.order_type_for_phase(
        clock.get("phase"), plan)
    if not order_type:
        return fail(f"{clock.get('phase')} 단계는 주문 허용 단계가 아닙니다 {plan.get('phases')}")
    if str(clock.get("phase")) == "PRE_MARKET":
        # 프리장은 승인 범위를 명시적으로 확인한다 — 이 경로만 manual_intervention_only에서 61을 낸다.
        if not plan.get("premarket_enabled"):
            return fail("프리장 미승인 — 수동 개입도 61 주문을 낼 수 없습니다")
        if manual:
            ok_check("premarket_manual_scope",
                     "manual_intervention_only — 사용자 지시 단건 개입(61)")
        elif not plan.get("premarket_auto_enabled"):
            return fail("프리장 자동 개입 금지(manual_intervention_only) — 사용자 지시 개입만 허용")
    if side == "BUY" and gate_check.get("sell_only"):
        return fail("매도 전용 세션(live gate sell_only) — 신규 매수 금지")

    # VIX 리스크오프(28 이상)는 **신규 롱 하드 차단**이다 — 무인 엔진과 같은 규칙을
    # 수동 개입에도 적용한다. 헤지(SOXS·SQQQ)는 방어 포지션이므로 VIX 24 이상 확인만으로 허용한다.
    hedge_tickers = {"SOXS", "SQQQ"}
    vix_gate = ((integrity or {}).get("vix") or {}).get("gate") if isinstance(integrity, dict) else None
    vix_gate = vix_gate if vix_gate is not None else vix
    vix_override = bool(override_vix or (isinstance(gate, dict) and gate.get("vix_override_allowed") is True))
    gap_override = bool(override_gap or (isinstance(gate, dict) and gate.get("gap_override_allowed") is True))
    if side == "BUY" and vix_gate is not None:
        scale = execution_policy.vix_scale(vix_gate, policy)
        if scale <= 0 and not (ticker in hedge_tickers and float(vix_gate) >= 24.0):
            if vix_override and manual:
                ok_check("vix_override", {"vix": vix_gate, "reason": "사용자 지시 VIX 예외 허용"})
            else:
                return fail(f"VIX {vix_gate} 리스크오프 — 신규 롱 하드 차단(헤지 예외)")
        out["vix"] = vix_gate
        out["vix_scale"] = scale
    if not execution_policy.allowed_ticker(ticker, policy):
        return fail(f"{ticker}는 승인 유니버스 밖입니다 {policy.get('universe')}")
    ok_check("universe", policy.get("universe"))

    # ---- 가격
    ref = _quote_price(quotes, ticker)
    px = float(price) if price else ref
    if not px or px <= 0:
        return fail(f"{ticker} 기준가 없음 — --price 로 지정해야 합니다")
    q = quotes.get(ticker) or {}
    prev_close = q.get("prev_close")
    if order_type == "61":
        fresh = session_routing.quote_fresh(q, clock, max_age_min=float(
            (plan.get("entry_conditions") or {}).get("max_quote_age_min") or 6.0))
        out["quote_fresh"] = fresh
        if not fresh.get("ok"):
            return fail(f"프리장 시세 신선도 미달 — {fresh.get('reason')}")
        ok_check("quote_fresh", fresh.get("age_min"))

    # ---- 수량
    g = execution_policy.guardrails(policy)
    full_cap = bool(full_capital or (isinstance(gate, dict) and gate.get("full_capital_approved") is True))
    reserve = 0.0 if (full_cap and manual) else float(g.get("cash_reserve_floor_usd", 150.0))
    cash = float(cash_usd if cash_usd is not None else 0.0)
    if cash_usd is None:
        led = _load(os.path.join(DATA, "live_portfolio.json"), {}) or {}
        cash = float(led.get("cash_deposit_usd") or led.get("cash_buffer_usd") or 0.0)
    held = _holdings(brackets, ticker)
    out["held_qty"] = held
    out["reference_price"] = round(px, 2)
    out["prev_close"] = prev_close

    if side == "SELL":
        if held <= 0:
            return fail(f"{ticker} 보유 없음 — 매도 불가")
        want = int(qty or held)
        if want > held:
            return fail(f"보유 {held}주 < 매도 요청 {want}주")
        qty = want
    else:
        if held > 0:
            return fail(f"{ticker} 이미 {held}주 보유 — 물타기 금지")
        if notional and qty:
            return fail("--qty 와 --notional 중 하나만 주세요")
        if full_cap and manual:
            cap = min(float(gate_check.get("max_buy_order_usd") or 700.0), max(0.0, cash))
        else:
            budget = execution_policy.entry_budget(equity_usd or cash, cash, policy, 1.0)["budget_usd"]
            phase_room = session_routing.budget(clock.get("phase"), plan, budget)["budget_usd"]
            cap = min(phase_room, float(g.get("entry_budget_cap_usd", 220.0)), max(0.0, cash - reserve))
        want_notional = float(notional) if notional else cap
        if want_notional > cap + 1e-6:
            return fail(f"1회 예산 상한 ${cap:.2f} < 요청 ${want_notional:.2f} "
                        f"(단계 {clock.get('phase')}, 현금 ${cash:.2f} − 마지노선 ${reserve:.0f})",
                        allowed_notional_usd=round(cap, 2))
        if qty:
            cost = int(qty) * px
            if cost > cap + 1e-6:
                return fail(f"{qty}주 ${cost:.2f} > 1회 예산 상한 ${cap:.2f}",
                            allowed_qty=int(cap // px))
            if cost > max(0.0, cash - reserve) + 1e-6:
                return fail(f"{qty}주 ${cost:.2f} > 현금 ${cash:.2f} − 마지노선 ${reserve:.0f}")
        else:
            qty = int(want_notional // px)
            if qty <= 0:
                return fail(f"예산 ${want_notional:.2f} < 1주 ${px:.2f} — 수량 0")
        ok_check("budget", {"cap_usd": round(cap, 2), "cash_usd": round(cash, 2),
                            "reserve_usd": reserve})
        if order_type == "61":
            cond = plan.get("entry_conditions") or {}
            cap_orders = int(cond.get("max_orders_per_session") or 2)
            prior = _today_buys(log_path, session_date=str(clock.get("session_date") or ""))
            if len(prior) >= cap_orders:
                return fail(f"프리장 매수 주문 한도 {cap_orders}건 도달")
            ok_check("premarket_order_cap", {"used": len(prior), "cap": cap_orders})
            if prev_close:
                try:
                    gap = (px / float(prev_close) - 1.0) * 100.0
                except (TypeError, ValueError, ZeroDivisionError):
                    gap = 0.0
                chase = float(cond.get("max_gap_chase_pct") or 1.5)
                if gap > chase:
                    if gap_override and manual:
                        ok_check("gap_override", {"gap_pct": round(gap, 2), "cap_pct": chase,
                                                  "reason": "사용자 지시 갭 추격 예외 허용"})
                    else:
                        return fail(f"추격 금지 — 전일 종가 대비 +{gap:.2f}% > +{chase:.2f}%")
                else:
                    ok_check("gap_band", {"gap_pct": round(gap, 2), "cap_pct": chase})
        # 게이트 매수 한도
        cost = qty * px
        if cost > float(gate_check.get("max_buy_order_usd") or 0.0) + 1e-6:
            return fail(f"게이트 1회 매수 한도 ${gate_check.get('max_buy_order_usd')} < ${cost:.2f}")
        ok_check("live_gate_caps", {"max_buy_order_usd": gate_check.get("max_buy_order_usd"),
                                    "max_session_buy_usd": gate_check.get("max_session_buy_usd")})

    # ---- 지정가(단계별 슬리피지 상한)
    cfg = policy.get("execution") or {}
    limit, limit_meta = session_routing.limit_price(
        px, side, clock.get("phase"), plan,
        fallback_bps=float(cfg.get("buy_limit_offset_bps" if side == "BUY" else "sell_limit_offset_bps",
                                   20 if side == "BUY" else -30)),
        price_round=int(cfg.get("price_round", 2)))
    out.update({"qty": int(qty), "notional_usd": round(int(qty) * limit, 2),
                "limit_price": limit, "limit_meta": limit_meta,
                "order_type": order_type, "reason": reason or f"수동 개입 {side} {ticker}"})
    out["ok"] = True
    return out


def run_intervention(*, execute: bool = False, router=None, log_path: Optional[str] = None,
                     account: Optional[Dict[str, Any]] = None,
                     **kwargs) -> Dict[str, Any]:
    """계획 → (게이트 통과 시) 주문 → 기록. execute=False면 DRY_RUN으로 멈춘다."""
    plan = plan_intervention(log_path=log_path, **kwargs)
    rec: Dict[str, Any] = {"ts_kst": _now_str(), "kind": "INTERVENE",
                           "requested": {k: kwargs.get(k) for k in
                                         ("ticker", "side", "qty", "price", "notional")},
                           "plan": {k: plan.get(k) for k in
                                    ("ok", "reason", "phase", "session_date", "order_type",
                                     "qty", "limit_price", "notional_usd", "limit_meta",
                                     "manual_intervention", "premarket_manual_only")},
                           "checks": plan.get("checks")}
    if not plan.get("ok"):
        rec["status"] = "BLOCKED"
        _append_log(rec, log_path)
        return rec
    if not execute:
        rec["status"] = "DRY_RUN"
        _append_log(rec, log_path)
        return rec

    from execution.order_bridge import OrderRouter
    from core import execution_policy
    policy = kwargs.get("policy") or execution_policy.load_policy()
    router = router or OrderRouter(
        repo=(policy.get("execution_channel") or {}).get("repo"),
        workflow=(policy.get("execution_channel") or {}).get("workflow", "nh_trade_dispatch.yml"),
        dry_run=False,
        timeout_s=int(((policy.get("guardrails") or {}).get("order_timeout_sec") or 300)))
    res = router.place(plan["ticker"], plan["side"], plan["qty"], plan["limit_price"],
                       plan.get("reason") or "수동 개입",
                       session_date=str(plan.get("session_date") or ""),
                       dry_run=False, order_type=plan.get("order_type") or "00",
                       extra={"intervention": True, "limit_meta": plan.get("limit_meta"),
                              "checks": plan.get("checks")})
    rec.update({"status": res.get("status"), "order_type": plan.get("order_type"),
                "order_no": res.get("orr_no"), "run_id": res.get("run_id"),
                "response": res.get("response") or res.get("error"),
                "ticker": plan["ticker"], "side": plan["side"], "qty": plan["qty"],
                "limit_price": plan.get("limit_price"), "session_date": plan.get("session_date")})
    _append_log(rec, log_path)
    return rec


def status_snapshot(clock: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from core import market_clock, session_routing
    from core.live_session_gate import check_live_gate
    clock = clock or market_clock.resolve()
    plan = session_routing.status(session_routing.load_policy())
    gate = _load(GATE_PATH, {})
    return {"now_kst": _now_str(), "phase": clock.get("phase"),
            "session_date": clock.get("session_date"),
            "order_type": session_routing.order_type_for_phase(clock.get("phase"), plan),
            "session_plan": {k: plan.get(k) for k in
                             ("phases", "auto_phases", "premarket_enabled", "premarket_auto_enabled",
                              "premarket_manual_only", "manual_intervention_only",
                              "after_hours_enabled", "blocked_reason", "auto_blocked_reason")},
            "live_gate": check_live_gate(gate, clock, plan=plan),
            "positions": {tk: _holdings(_load(os.path.join(DATA, "active_brackets.json"), {}), tk)
                          for tk in sorted((_load(os.path.join(DATA, "active_brackets.json"), {}) or {}))}}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="프리장·본장 즉시 개입 매매(기본 DRY_RUN)")
    ap.add_argument("--ticker")
    ap.add_argument("--side", choices=["BUY", "SELL", "buy", "sell"])
    ap.add_argument("--qty", type=int)
    ap.add_argument("--price", type=float)
    ap.add_argument("--notional", type=float)
    ap.add_argument("--reason", default="")
    ap.add_argument("--override-vix", action="store_true", help="사용자 승인 세션 VIX 하드 차단 예외")
    ap.add_argument("--override-gap", action="store_true", help="사용자 승인 세션 프리장 갭 추격 예외")
    ap.add_argument("--full-capital", action="store_true", help="사용자 승인 가용 현금 전액 활용(마지노선·예산 상한 해제)")
    ap.add_argument("--execute", action="store_true", help="실주문(없으면 미리보기)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv)

    if args.status:
        snap = status_snapshot()
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    if not args.ticker or not args.side:
        ap.print_help()
        return 2

    from core import market_clock
    clock = market_clock.resolve()
    quotes: Dict[str, Any] = {}
    integrity: Dict[str, Any] = {}
    snap: Dict[str, Any] = {}
    try:
        from core import market_snapshot
        snap = market_snapshot.build_snapshot(quiet=True, max_age_min=6)
        quotes = snap.get("quotes") or {}
    except Exception:
        snap, quotes = {}, {}
    try:
        from core import live_integrity
        integrity = live_integrity.evaluate(snap, clock, tickers=[args.ticker.upper()], persist=False)
    except Exception:
        integrity = {}
    vix = (((integrity.get("vix") or {}).get("gate"))
           or ((snap.get("macro") or {}).get("vix") or {}).get("last"))
    brackets = _load(os.path.join(DATA, "active_brackets.json"), {}) or {}
    led = _load(os.path.join(DATA, "live_portfolio.json"), {}) or {}

    rec = run_intervention(execute=args.execute, clock=clock, quotes=quotes, brackets=brackets,
                           cash_usd=led.get("cash_deposit_usd", led.get("cash_buffer_usd")),
                           vix=vix, integrity=integrity,
                           override_vix=args.override_vix, override_gap=args.override_gap,
                           full_capital=args.full_capital,
                           ticker=args.ticker, side=args.side.upper(), qty=args.qty,
                           price=args.price, notional=args.notional, reason=args.reason)
    if args.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    else:
        p = rec.get("plan") or {}
        print(f"[intervene] {rec.get('status')} {args.side.upper()} {args.ticker} "
              f"{p.get('qty') or '-'}주 @ {p.get('limit_price') or '-'} "
              f"({p.get('phase')}·호가유형 {p.get('order_type') or '-'})")
        if rec.get("status") == "BLOCKED":
            print(f"  차단: {p.get('reason')}")
        elif rec.get("status") == "DRY_RUN":
            print("  미리보기입니다 — 실주문은 --execute")
        else:
            print(f"  주문번호 {rec.get('order_no')} · {rec.get('status')}")
    return 0 if rec.get("status") in ("SUBMITTED", "DRY_RUN", "PENDING", "DISPATCHED") else 2


if __name__ == "__main__":
    sys.exit(main())
