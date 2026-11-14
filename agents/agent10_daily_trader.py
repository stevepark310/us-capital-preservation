"""
Agent 10: Execution Trader & Bracket Custodian

The previous version emitted four "FILLED_EXECUTED" orders with 11-11 prices and
hard-coded P&L inside an hourly report — i.e. it pretended to have executed
trades that did not exist. Now it:

  * reads the real ledger (single FX rate, one cash figure) and checks it,
  * re-computes every open bracket from the realised fill (fill-based anchoring),
  * produces a Monday-open plan with prices, brackets and cash allocation,
  * plans the validated DIP_SWING signal as an overnight-capable order only when
    config/swing_approval.json carries the user's approval (approved 2026-11-14
    21:29 KST), and keeps the intraday rotation running in parallel,
  * never marks an order as filled.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent
from core import ledger as ledger_mod, technical, strategy_approval
from core.exit_planner import AdaptiveExitPlanner


class DailyTraderAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=10,
            name="Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
            code="SHORT_TERM_EXECUTION_TRADER",
            role="실전 원장 정합성·브래킷 재산정·개장 대기 주문 계획",
            description="실전 원장과 활성 브래킷을 검증하고, 체결가 기준으로 매도 브래킷을 재산정하며, 다음 개장(23:30 KST) 대기 계획과 현금 배분을 보고합니다. 주문 집행은 시장 개장 시 별도 확인 절차를 따릅니다.",
        )
        self.planner = AdaptiveExitPlanner()

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        snap = context.get("snapshot") or {}
        clock = context.get("clock") or {}
        techs = context.get("technical") or {}
        ensemble = context.get("ensemble") or {}
        alpha = context.get("alpha") or {}
        dip = (context.get("technical_report") or {}).get("dip_swing_watchlist") or {}

        review = ledger_mod.validate(snapshot=snap, fix=True)
        led = ledger_mod.summary(snapshot=snap)
        phase = clock.get("phase", "UNKNOWN")
        quotes = snap.get("quotes", {})

        # ---- open bracket review, re-anchored on the realised fill
        bracket_review: List[Dict[str, Any]] = []
        for ticker, b in (led.get("active_brackets") or {}).items():
            if not isinstance(b, dict) or "entry_price" not in b:
                continue
            if b.get("carry_parking") or b.get("note") == "CARRY_PARKING":
                bracket_review.append({"ticker": ticker, "qty": int(b.get("bought_qty", 1) or 1),
                                       "entry_fill": b["entry_price"], "status": "CARRY_PARKING (익절·손절 대상 아님)",
                                       "current_bracket": {}, "recomputed": {}, "diff_note": ["SGOV은 무위험 캐리 파킹"]})
                continue
            qty = int(b.get("bought_qty", 0) or 0)
            filled = bool(b.get("tp1_filled")) and bool(b.get("tp2_filled")) and int(b.get("runner_qty", 0) or 0) == 0
            atr_pct = (techs.get(ticker) or {}).get("atr_pct") or 6.0
            pos = next((p for p in led.get("positions", []) if p["ticker"] == ticker), None)
            hard_floor = float(b["sl"]) if ticker == "NVDA" else None
            plan = self.planner.plan(
                symbol=ticker, planned_price=b.get("planned_price", b["entry_price"]),
                fill_price=b["entry_price"], qty=qty, atr_pct=atr_pct,
                sentiment_score=(context.get("sentiment") or {}).get("tickers", {}).get(ticker, {}).get("sentiment_score", 50),
                hard_floor_price=hard_floor, allow_runner=int(b.get("runner_qty", 0) or 0) > 0,
            )
            last = (quotes.get(ticker) or {}).get("last")
            locked = bool(b.get("levels_locked"))
            bracket_review.append({
                "ticker": ticker, "qty": qty, "entry_fill": b["entry_price"], "qty_open": int(b.get("runner_qty", 0) or 0),
                "current_bracket": {"tp1": b.get("tp1"), "tp2": b.get("tp2"), "sl": b.get("sl")},
                "recomputed": {"tp1": plan.tp1_price, "tp2": plan.tp2_price, "sl": plan.hard_sl_price,
                               "breakeven": plan.breakeven_price, "atr_pct": atr_pct},
                "levels_locked": locked,
                "diff_note": ((b.get("levels_locked_reason") or "사용자 지정 레벨 유지") if locked else plan.notes),
                "status": ("CLOSED (TP1·TP2 전량 체결, 러너 0주)" if filled and qty else
                           ("HOLDING" if pos else "NO_POSITION")),
                "last_price": last,
                "pnl_usd": (pos or {}).get("unrealized_usd"),
            })

        # ---- next-session plan
        equity = led.get("equity_usd") or 0.0
        cash = led.get("cash_usd") or 0.0
        weights = ensemble.get("target_portfolio_weights", {})
        dip_signals = dip.get("signals") or []
        dip_watch = dip.get("watch") or []
        mode = alpha.get("recommended_execution_mode")
        def size_by_risk(price: float, stop_pct: float, risk_pct: float = 0.02, cap_pct: float = 0.25) -> Dict[str, Any]:
            """Integer-share sizing from the risk budget, capped by position weight."""
            if not price or price <= 0 or stop_pct <= 0:
                return {"qty": 0, "cost_usd": 0.0, "risk_usd": 0.0, "risk_pct": 0.0}
            risk_budget = equity * risk_pct
            stop_dist = price * stop_pct / 100.0
            qty = max(1, int(risk_budget // stop_dist))
            if qty * price > equity * cap_pct:
                qty = max(1, int((equity * cap_pct) // price))
            cost = round(qty * price, 2)
            return {"qty": qty, "cost_usd": cost, "risk_usd": round(qty * stop_dist, 2),
                    "risk_pct": round(qty * stop_dist / equity * 100, 2) if equity else 0.0}

        planned: List[Dict[str, Any]] = []
        # ---- overnight approval gate: overnight holding is the user's risk call
        appr = strategy_approval.load()
        appr_block = strategy_approval.plan_block(appr)
        overnight_ok = appr_block["approved"]
        max_pos = int(appr_block.get("max_positions") or 2)
        dip_status = (f"APPROVED (오버나이트 보유 승인 · 최대 {appr_block.get('max_hold_sessions')}세션)"
                      if overnight_ok else "PENDING_APPROVAL (오버나이트 보유 승인 필요)")
        wait_status = ("APPROVED (오버나이트 승인됨 · 트리거 미충족 대기)"
                       if overnight_ok else "PENDING_APPROVAL (오버나이트 보유 승인 필요)")

        if mode == "DIP_SWING" and dip_signals and overnight_ok:
            for s in dip_signals[:max_pos]:
                planned.append({
                    "ticker": s["symbol"], "action": "BUY_AT_NEXT_OPEN (DIP_SWING)",
                    "reference_price": s["close"],
                    "validated_tp": s["validated_tp"], "validated_sl": s["validated_sl"],
                    "tp_pct": s["validated_tp_pct"], "sl_pct": s["validated_sl_pct"],
                    "max_hold_sessions": s["max_hold_sessions"],
                    "risk_per_trade_pct": 2.0,
                    **size_by_risk(s["close"], s["validated_sl_pct"]),
                    "bracket_rule": "진입 시점 ATR×1.5 대칭 TP/SL · 최대 3세션 시간청산",
                    "approval": f"{appr_block['status']} ({appr_block.get('approved_at_kst')})",
                    "status": dip_status,
                })
        elif mode == "DIP_SWING" and dip_signals and not overnight_ok:
            for s in dip_signals[:max_pos]:
                planned.append({
                    "ticker": s["symbol"], "action": "BUY_AT_NEXT_OPEN (DIP_SWING·보류)",
                    "reference_price": s["close"],
                    "validated_tp": s["validated_tp"], "validated_sl": s["validated_sl"],
                    "tp_pct": s["validated_tp_pct"], "sl_pct": s["validated_sl_pct"],
                    "max_hold_sessions": s["max_hold_sessions"],
                    "risk_per_trade_pct": 2.0,
                    **size_by_risk(s["close"], s["validated_sl_pct"]),
                    "status": dip_status,
                })
        elif mode == "DIP_SWING" and not dip_signals:
            for w in dip_watch[:max_pos]:
                planned.append({
                    "ticker": w["symbol"], "action": "WAIT_FOR_DIP_TRIGGER",
                    "condition": w.get("why_not"),
                    "validated_tp": w["validated_tp"], "validated_sl": w["validated_sl"],
                    "tp_pct": w["validated_tp_pct"], "sl_pct": w["validated_sl_pct"],
                    **size_by_risk(w["close"], w["validated_sl_pct"]),
                    "bracket_rule": "진입 시점 ATR×1.5 대칭 TP/SL · 최대 3세션 시간청산",
                    "approval": f"{appr_block['status']} ({appr_block.get('approved_at_kst')})",
                    "status": wait_status,
                })

        # ---- intraday rotation stays on (11-14 01:38 지시) — DIP_SWING은 병행, 대체 아님
        if mode and mode != "CASH_ONLY":
            for sym, w in weights.items():
                if sym == "SGOV" or w <= 0:
                    continue
                t = techs.get(sym) or {}
                if not t:
                    continue
                planned.append({
                    "ticker": sym, "action": "WATCH_BREAKOUT",
                    "trigger": (t.get("breakout_trigger") or (t.get("weekly_pivots") or {}).get("pivot")),
                    "target_weight": w, "size_guide_usd": round(equity * w, 2),
                    "bracket": f"ATR {t.get('atr_pct')}% 기준 (core/exit_planner 재산정)",
                    "status": "PLANNED (장중 회전 병행 · 개장 후 신호 확인)",
                })

        reanchor_needed = [b for b in bracket_review if b["status"] == "HOLDING" and b.get("current_bracket", {}).get("sl")]
        cash_krw = led.get("cash_krw")
        self.output_data = {
            "phase": phase, "phase_label": clock.get("label_ko"),
            "execution_broker": "NH투자증권 (나무) Open API",
            "account_number": led.get("account_no"),
            "ledger": {
                "fx_rate": led.get("fx_rate"), "fx_source": led.get("fx_source"),
                "realized_pnl_usd": led.get("realized_pnl_usd"), "unrealized_pnl_usd": led.get("unrealized_pnl_usd"),
                "net_pnl_usd": led.get("net_pnl_usd"),
                "equity_usd": equity, "equity_krw": led.get("equity_krw"),
                "cash_usd": cash, "cash_krw": cash_krw, "cash_pct": led.get("cash_pct"),
                "win_rate_pct": led.get("win_rate_pct"), "trades": led.get("trades"),
                "as_of": led.get("as_of"),
            },
            "consistency_check": {"status": review["status"], "issues": review["issues"],
                                  "fixed_trading_day_flags": review.get("fixed_days", [])},
            "bracket_review": bracket_review,
            "reanchor_required": [{"ticker": b["ticker"], "recomputed_sl": b["recomputed"]["sl"],
                                   "current_sl": b["current_bracket"].get("sl")} for b in reanchor_needed],
            "planned_actions": planned,
            "execution_status": ("NO_TRADE_WINDOW (주말·휴장)" if clock.get("is_off_hours") else "MONITORING"),
            "next_open_kst": clock.get("next_open_kst"),
            "swing_mode": {"recommended": mode == "DIP_SWING", "signals": dip_signals, "watch": dip_watch,
                           "overnight_approval": appr_block,
                           "policy": ("오버나이트 보유는 config/swing_approval.json의 사용자 승인(2026-11-14 21:29)으로 활성화됨. "
                                      "DIP_SWING은 장중 회전 매매와 병행하며 대체하지 않습니다." if overnight_ok else
                                      "오버나이트 보유는 리스크 프로파일 변경이므로 사용자 승인 전 집행하지 않습니다.")},
            "capital_plan": {
                "cash_usd": cash,
                "dip_swing_cap_usd": round(equity * float(appr_block.get("position_cap_pct") or 25) / 100.0 * max_pos, 2),
                "rule": "DIP_SWING 1순위(최대 2종목·종목 상한 25%) → 잔여 현금으로 장중 회전 병행",
                "max_positions": max_pos,
            },
            "key_takeaway": (
                f"{clock.get('label_ko')}. 원장 정합성 {review['status']} (이슈 {len(review['issues'])}건, "
                f"거래일 플래그 {len(review.get('fixed_days', []))}건 수정). "
                f"현금 ${cash:,.2f} / {cash_krw:,}원 ({led.get('cash_pct')}%), 평가 ${equity:,.2f}. "
                f"DIP_SWING 오버나이트 {appr_block['status']}. "
                f"다음 개장 {clock.get('next_open_kst')}. 대기 계획 {len(planned)}건."
            ),
        }
        self.confidence_score = 0.94
        self.status = "COMPLETED"
        return self.output_data
