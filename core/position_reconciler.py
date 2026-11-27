"""
Position reconciler — 실전 원장(live_portfolio.json)만 보유의 정본으로 인정한다.

왜 필요한가
-----------
2026-11-18 00:32·01:22에 `SOXL 2주`·`TQQQ 2주`가 손절로 전량 청산됐는데도
`data/avg_anchor_plan.json`(→ `cube_report.profit_mandate` → 대시보드)에는 두 종목의
사다리가 그대로 남아 있었다. 청산된 주식을 계속 '관리 대상 포지션'으로 세면
① 기대 익절/손실 합계가 틀리고 ② 직원들이 없는 포지션의 청산을 판단하며
③ 대시보드가 실계좌와 다른 그림을 보여 준다.

이 모듈은 보유 정본을 하나로 고정한다.

    정본 = data/live_portfolio.json 의 positions (NH 잔고 동기화 결과)

그리고 파생 파일 세 곳을 정본과 100% 일치시킨다.

1. `data/avg_anchor_plan.json` — 원장에 없는 종목의 사다리는 제거, 수량이 다르면 수량만 맞춘다.
2. `data/active_brackets.json` — 미청산인데 원장에 없는 브래킷은 `ghost`로 표시하고 청산 처리한다
   (기록은 지우지 않는다. 감사 추적은 남기고 '보유'로만 세지 않게 한다).
3. `data/autotrader_state.json` — 원장에 없는 종목의 미확인 SELL 주문은 취소 처리한다
   (BUY 주문은 체결 대기이므로 건드리지 않는다).

안전장치
--------
* 원장을 읽지 못했거나 `api_status != CONNECTED`면 아무것도 바꾸지 않는다.
* 원장 보유가 0건인데 미청산 브래킷이 있으면 자동 정리를 건너뛴다 — 빈 원장은
  '전량 매도'가 아니라 '동기화 실패'일 수 있기 때문이다(그 경우는 체결 확인 경로가 처리한다).
* NVDA 사용자 잠금 레벨(`levels_locked`)은 수량 보정만 하고 레벨은 절대 건드리지 않는다.

CLI
---
    python3 -m core.position_reconciler            # 점검만 (변경 없음)
    python3 -m core.position_reconciler --apply    # 정리 적용
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")

LIVE_PATH = os.path.join(DATA, "live_portfolio.json")
PLAN_PATH = os.path.join(DATA, "avg_anchor_plan.json")
BRACKETS_PATH = os.path.join(DATA, "active_brackets.json")
STATE_PATH = os.path.join(DATA, "autotrader_state.json")
REPORT_PATH = os.path.join(DATA, "position_reconciliation.json")

CARRY_TICKERS = {"SGOV"}


def now_str() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def live_holdings(live: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """원장 보유 종목 → {ticker: {qty, avg_price}} (수량 0은 제외)."""
    live = live if live is not None else _load(LIVE_PATH, {})
    out: Dict[str, Dict[str, Any]] = {}
    for p in (live.get("positions") or []):
        if not isinstance(p, dict):
            continue
        tk = str(p.get("ticker") or "").upper()
        try:
            qty = int(p.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if not tk or qty <= 0:
            continue
        out[tk] = {"qty": qty, "avg_price": p.get("avg_price"), "source": "live_portfolio"}
    return out


def _open_bracket(b: Any) -> bool:
    if not isinstance(b, dict) or "entry_price" not in b:
        return False
    if b.get("carry_parking") or b.get("note") == "CARRY_PARKING":
        return False
    if b.get("closed"):
        return False
    try:
        return int(b.get("bought_qty") or 0) > 0
    except (TypeError, ValueError):
        return False


def reconcile(apply: bool = False, live: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """원장·사다리·브래킷·집행상태를 한 번에 점검(및 정리)한다."""
    live = live if live is not None else _load(LIVE_PATH, {})
    checked_at = now_str()
    report: Dict[str, Any] = {
        "checked_at_kst": checked_at,
        "applied": bool(apply),
        "status": "CONSISTENT",
        "holdings": {},
        "ghost_positions": [],
        "qty_fixes": [],
        "bracket_ghosts": [],
        "pending_cancelled": [],
        "notes": [],
    }

    if not live or live.get("api_status") != "CONNECTED":
        report["status"] = "SKIPPED_NO_LEDGER"
        report["notes"].append("live_portfolio.json 없음 또는 api_status != CONNECTED — 정리를 건너뜁니다.")
        _save(REPORT_PATH, report)
        return report

    holdings = live_holdings(live)
    report["holdings"] = {k: v["qty"] for k, v in holdings.items()}
    live_synced = str(live.get("last_synced") or "")

    plan = _load(PLAN_PATH, {})
    brackets = _load(BRACKETS_PATH, {})
    state = _load(STATE_PATH, {})

    open_brackets = {k: v for k, v in brackets.items() if _open_bracket(v)}
    if not holdings and open_brackets:
        report["status"] = "SKIPPED_EMPTY_LEDGER"
        report["notes"].append(
            f"원장 보유 0건인데 미청산 브래킷 {len(open_brackets)}건이 있습니다 "
            f"({', '.join(open_brackets)}). 빈 원장은 동기화 실패일 수 있어 자동 정리를 건너뜁니다.")
        _save(REPORT_PATH, report)
        return report

    # ---------------------------------------------------------------- 1. 사다리
    ladders = plan.get("ladders") if isinstance(plan, dict) else None
    if isinstance(ladders, list):
        kept: List[Dict[str, Any]] = []
        for lad in ladders:
            tk = str((lad or {}).get("ticker") or "").upper()
            held = holdings.get(tk)
            if not held:
                report["ghost_positions"].append({
                    "ticker": tk, "qty": lad.get("qty"), "avg_price": lad.get("avg_price"),
                    "where": "avg_anchor_plan.ladders",
                    "reason": "원장에 없는 보유(청산 완료)" if tk else "티커 없음",
                })
                continue
            try:
                plan_qty = int(lad.get("qty") or 0)
            except (TypeError, ValueError):
                plan_qty = 0
            if plan_qty != int(held["qty"]):
                report["qty_fixes"].append({"ticker": tk, "where": "avg_anchor_plan.ladders",
                                            "plan_qty": plan_qty, "live_qty": int(held["qty"])})
                lad = dict(lad)
                lad["qty"] = int(held["qty"])
                lad["qty_reconciled_from"] = plan_qty
            kept.append(lad)
        if apply and (report["ghost_positions"] or report["qty_fixes"]):
            plan = dict(plan)
            plan["ladders"] = kept
            plan["positions_under_management"] = [l.get("ticker") for l in kept]
            summary = dict(plan.get("summary") or {})
            if summary:
                # 기대 익절·손실·측정 기대값을 남은 사다리에서 다시 합산한다 —
                # 청산된 포지션의 기대값이 합계에 남아 있으면 숫자가 틀린다.
                tp_total = round(sum((l.get("expected_pnl_usd") or {}).get("tp1", 0.0)
                                      + ((l.get("expected_pnl_usd") or {}).get("tp2") or 0.0)
                                      for l in kept if not l.get("levels_locked")), 2)
                sl_total = round(sum((l.get("expected_pnl_usd") or {}).get("sl_full", 0.0)
                                      for l in kept if not l.get("levels_locked")), 2)
                measured = 0.0
                for l in kept:
                    m = l.get("measured") or {}
                    if m.get("available"):
                        measured += (m.get("ev_pct_per_share") or 0.0) / 100.0 * (l.get("avg_price") or 0) * (l.get("qty") or 0)
                summary["positions_under_management"] = [l.get("ticker") for l in kept]
                summary["expected_tp_total_usd"] = tp_total
                summary["max_loss_at_sl_usd"] = sl_total
                summary["expected_best_case_usd"] = tp_total
                summary["expected_worst_case_usd"] = sl_total
                summary["locked_positions_risk_usd"] = round(sum(
                    (l.get("expected_pnl_usd") or {}).get("sl_full", 0.0) for l in kept if l.get("levels_locked")), 2)
                summary["measured_expected_usd_per_session"] = round(measured, 2)
                summary["recomputed_from_ledger_at_kst"] = checked_at
            plan["summary"] = summary or plan.get("summary")
            plan["reconciled_at_kst"] = checked_at
            plan["reconciled_note"] = ("원장(live_portfolio.json) 기준 재정리 — "
                                       "청산 완료 종목 사다리 제거, 수량·기대값 재합산")
            _save(PLAN_PATH, plan)

    # ---------------------------------------------------------------- 2. 브래킷
    for tk, b in list(brackets.items()):
        if not _open_bracket(b):
            continue
        if tk.upper() in holdings:
            continue
        report["bracket_ghosts"].append({"ticker": tk, "bought_qty": b.get("bought_qty"),
                                         "entry_price": b.get("entry_price"),
                                         "where": "active_brackets"})
        if apply:
            b = dict(b)
            b["closed"] = True
            b["ghost"] = True
            b["ghost_reason"] = f"원장에 없는 보유 — 유령 포지션 정리({checked_at[:10]})"
            b["ghost_detected_at_kst"] = checked_at
            b["sl_triggered"] = True
            b["levels_locked"] = b.get("levels_locked", False)   # 레벨 값은 보존
            brackets[tk] = b
    if apply and report["bracket_ghosts"]:
        _save(BRACKETS_PATH, brackets)

    # ---------------------------------------------------------------- 3. 미확인 주문
    if isinstance(state, dict) and state.get("pending"):
        keep = []
        for p in state["pending"]:
            tk = str((p or {}).get("ticker") or "").upper()
            side = str((p or {}).get("side") or "").upper()
            if side == "SELL" and tk not in holdings:
                report["pending_cancelled"].append({"ticker": tk, "qty": p.get("qty"),
                                                    "order_no": p.get("order_no"),
                                                    "reason": "원장에 없는 보유의 매도 주문"})
                continue
            keep.append(p)
        if apply and report["pending_cancelled"]:
            state["pending"] = keep
            state["reconciled_at_kst"] = checked_at
            _save(STATE_PATH, state)

    if report["ghost_positions"] or report["bracket_ghosts"] or report["qty_fixes"] or report["pending_cancelled"]:
        report["status"] = "RECONCILED" if apply else "ISSUES_FOUND"
    report["ghost_count"] = len(report["ghost_positions"])
    report["live_synced"] = live_synced
    report["open_brackets"] = sorted(open_brackets.keys())
    include = sorted({str((l or {}).get("ticker") or "").upper() for l in (ladders or [])})
    if apply and (report["ghost_positions"] or report["qty_fixes"]):
        # 파일을 실제로 정리했으면 '정리 후' 목록으로 일치 여부를 판단한다.
        include = sorted({str((l or {}).get("ticker") or "").upper()
                          for l in (plan.get("ladders") if isinstance(plan, dict) else []) or []})
    ladder_scope = {k for k in holdings if k not in CARRY_TICKERS}
    # 캐리 파킹(SGOV)만 보유한 상태는 '사다리로 관리할 포지션이 없다'는 뜻이다 — 사다리 목록이
    # 비어 있는 것과 같으므로 일치다(종전에는 bool({}) == False로 항상 '확인 필요'를 띄웠다).
    report["ladder_scope"] = sorted(ladder_scope)
    report["carry_holdings"] = {k: q for k, q in (report.get("holdings") or {}).items() if k in CARRY_TICKERS}
    report["ledger_match"] = set(include) == ladder_scope
    _save(REPORT_PATH, report)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    rep = reconcile(apply="--apply" in args)
    print(f"[reconciler] {rep['status']} · 보유 {rep['holdings']} · "
          f"사다리 유령 {rep['ghost_count']} · 브래킷 유령 {len(rep['bracket_ghosts'])} · "
          f"수량보정 {len(rep['qty_fixes'])} · 대기주문취소 {len(rep['pending_cancelled'])}")
    for n in rep.get("notes") or []:
        print("  · " + n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
