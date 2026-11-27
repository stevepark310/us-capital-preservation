"""유령 포지션 정리 — 원장(live_portfolio.json)만 보유 정본으로 인정한다."""
import json

import pytest

from core import position_reconciler as pr


def write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    for attr, name in (("LIVE_PATH", "live_portfolio.json"), ("PLAN_PATH", "avg_anchor_plan.json"),
                       ("BRACKETS_PATH", "active_brackets.json"), ("STATE_PATH", "autotrader_state.json"),
                       ("REPORT_PATH", "position_reconciliation.json")):
        monkeypatch.setattr(pr, attr, str(tmp_path / name))
    return tmp_path


def live(positions, status="CONNECTED"):
    return {"api_status": status, "last_synced": "2026-11-19 10:31:46 KST",
            "positions": [{"ticker": t, "qty": q, "avg_price": a} for t, q, a in positions]}


def plan_with(tickers):
    return {"engine": "AVG_ANCHOR_PROFIT_DESK",
            "ladders": [{"ticker": t, "qty": q, "avg_price": 50.0,
                         "expected_pnl_usd": {"tp1": 1.0, "tp2": 1.0, "sl_full": -2.0}}
                        for t, q in tickers],
            "summary": {"positions_under_management": [t for t, _ in tickers],
                        "expected_best_case_usd": 4.0, "expected_worst_case_usd": -4.0}}


def test_ghost_ladders_are_removed_and_totals_recomputed(paths):
    write(paths, "live_portfolio.json", live([("NVDA", 1, 173.3), ("SGOV", 1, 100.53)]))
    write(paths, "avg_anchor_plan.json", plan_with([("NVDA", 1), ("SOXL", 2), ("TQQQ", 2)]))
    write(paths, "active_brackets.json", {})

    rep = pr.reconcile(apply=True)
    assert rep["status"] == "RECONCILED"
    assert {g["ticker"] for g in rep["ghost_positions"]} == {"SOXL", "TQQQ"}
    assert rep["ghost_count"] == 2
    assert rep["ledger_match"] is True

    plan = json.loads((paths / "avg_anchor_plan.json").read_text(encoding="utf-8"))
    assert [l["ticker"] for l in plan["ladders"]] == ["NVDA"]
    assert plan["summary"]["positions_under_management"] == ["NVDA"]
    # 합계는 남은 사다리에서 다시 계산한다(청산 종목 기대값이 남으면 숫자가 틀린다)
    assert plan["summary"]["expected_best_case_usd"] == 2.0
    assert plan["summary"]["expected_worst_case_usd"] == -2.0


def test_qty_mismatch_is_corrected_from_ledger(paths):
    write(paths, "live_portfolio.json", live([("TQQQ", 3, 54.0)]))
    write(paths, "avg_anchor_plan.json", plan_with([("TQQQ", 2)]))
    write(paths, "active_brackets.json", {})
    rep = pr.reconcile(apply=True)
    assert rep["qty_fixes"][0]["live_qty"] == 3
    plan = json.loads((paths / "avg_anchor_plan.json").read_text(encoding="utf-8"))
    assert plan["ladders"][0]["qty"] == 3


def test_open_bracket_without_holding_is_marked_ghost_not_deleted(paths):
    write(paths, "live_portfolio.json", live([("NVDA", 1, 173.3)]))
    write(paths, "avg_anchor_plan.json", plan_with([("NVDA", 1)]))
    write(paths, "active_brackets.json", {
        "NVDA": {"entry_price": 173.3, "bought_qty": 1, "sl": 150.0, "closed": False},
        "SOXL": {"entry_price": 50.43, "bought_qty": 2, "sl": 49.17, "closed": False,
                 "last_exit": {"price": 47.46}},
    })
    rep = pr.reconcile(apply=True)
    assert [g["ticker"] for g in rep["bracket_ghosts"]] == ["SOXL"]
    b = json.loads((paths / "active_brackets.json").read_text(encoding="utf-8"))
    assert b["SOXL"]["closed"] is True and b["SOXL"]["ghost"] is True
    # 기록은 지우지 않는다 — 감사 추적 유지
    assert b["SOXL"]["last_exit"]["price"] == 47.46
    assert b["SOXL"]["sl"] == 49.17
    assert b["NVDA"]["closed"] is False


def test_empty_ledger_with_open_brackets_is_skipped(paths):
    write(paths, "live_portfolio.json", live([]))
    write(paths, "avg_anchor_plan.json", plan_with([("SOXL", 2)]))
    write(paths, "active_brackets.json", {
        "SOXL": {"entry_price": 50.43, "bought_qty": 2, "sl": 49.17, "closed": False}})
    rep = pr.reconcile(apply=True)
    assert rep["status"] == "SKIPPED_EMPTY_LEDGER"
    b = json.loads((paths / "active_brackets.json").read_text(encoding="utf-8"))
    assert b["SOXL"]["closed"] is False          # 동기화 실패를 '전량 매도'로 오해하지 않는다


def test_disconnected_ledger_changes_nothing(paths):
    write(paths, "live_portfolio.json", live([("NVDA", 1, 173.3)], status="DISCONNECTED"))
    write(paths, "avg_anchor_plan.json", plan_with([("NVDA", 1), ("TQQQ", 2)]))
    write(paths, "active_brackets.json", {})
    rep = pr.reconcile(apply=True)
    assert rep["status"] == "SKIPPED_NO_LEDGER"
    plan = json.loads((paths / "avg_anchor_plan.json").read_text(encoding="utf-8"))
    assert len(plan["ladders"]) == 2


def test_pending_sell_of_unheld_ticker_is_cancelled(paths):
    write(paths, "live_portfolio.json", live([("NVDA", 1, 173.3)]))
    write(paths, "avg_anchor_plan.json", plan_with([("NVDA", 1)]))
    write(paths, "active_brackets.json", {})
    write(paths, "autotrader_state.json", {"pending": [
        {"ticker": "SOXL", "side": "SELL", "qty": 2, "order_no": 192126},
        {"ticker": "NVDA", "side": "BUY", "qty": 1},
    ]})
    rep = pr.reconcile(apply=True)
    assert [p["ticker"] for p in rep["pending_cancelled"]] == ["SOXL"]
    st = json.loads((paths / "autotrader_state.json").read_text(encoding="utf-8"))
    assert [p["ticker"] for p in st["pending"]] == ["NVDA"]


def test_apply_to_brackets_never_resurrects_ghost():
    from core import avg_anchor_engine
    plan = {"engine": "AVG_ANCHOR_PROFIT_DESK", "ladders": [
        {"ticker": "SOXL", "qty": 2, "avg_price": 50.43, "tp1": 51.94, "tp1_qty": 1,
         "tp2": 53.46, "tp2_qty": 1, "sl": 49.17, "breakeven": 50.6, "atr_pct": 25.9,
         "sl_active_after_kst": "23:45", "force_exit_kst": "05:40", "levels_locked": False},
    ]}
    brackets = {"SOXL": {"bought_qty": 2, "entry_price": 50.43, "closed": True,
                         "ghost": True, "ghost_reason": "원장에 없는 보유"}}
    merged = avg_anchor_engine.apply_to_brackets(plan, brackets, holdings={"NVDA": 1})
    assert merged["SOXL"]["closed"] is True and merged["SOXL"]["ghost"] is True

    merged2 = avg_anchor_engine.apply_to_brackets(plan, {}, holdings={"NVDA": 1})
    assert "SOXL" not in merged2        # 원장에 없으면 브래킷도 만들지 않는다


def test_profit_mandate_filters_ghost_ladders(tmp_path, monkeypatch):
    from agents import cube_orchestrator as orch
    plan = plan_with([("NVDA", 1), ("SOXL", 2), ("TQQQ", 2)])
    p = tmp_path / "avg_anchor_plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(orch, "MANDATE_FILE", str(p))
    live_p = tmp_path / "live_portfolio.json"
    live_p.write_text(json.dumps(live([("NVDA", 1, 173.3)]), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pr, "LIVE_PATH", str(live_p))

    mandate = orch._profit_mandate()
    assert [l["ticker"] for l in mandate["ladders"]] == ["NVDA"]
    assert mandate["ghost_positions_filtered"] == ["SOXL", "TQQQ"]
    assert mandate["summary"]["positions_under_management"] == ["NVDA"]


def test_carry_only_holding_counts_as_ledger_match(paths):
    """SGOV 캐리 파킹만 보유 — 관리할 사다리가 없으므로 '원장 일치'다(2026-11-28).

    종전에는 bool(ladder_scope) 조건 때문에 사다리 0건이면 항상 ledger_match=false였고,
    대시보드는 SGOV 1주만 든 정상 계좌에 '확인 필요'를 띄웠다.
    """
    write(paths, "live_portfolio.json", live([("SGOV", 1, 100.53)]))
    write(paths, "avg_anchor_plan.json", {"engine": "AVG_ANCHOR_PROFIT_DESK", "ladders": [],
                                          "summary": {"positions_under_management": []}})
    write(paths, "active_brackets.json", {})

    rep = pr.reconcile(apply=True)
    assert rep["holdings"] == {"SGOV": 1}
    assert rep["ledger_match"] is True
    assert rep["carry_holdings"] == {"SGOV": 1}
    assert rep["ladder_scope"] == []


def test_ladder_without_ledger_holding_is_not_a_match(paths):
    write(paths, "live_portfolio.json", live([("SGOV", 1, 100.53)]))
    write(paths, "avg_anchor_plan.json", plan_with([("NVDA", 1)]))
    write(paths, "active_brackets.json", {})

    rep = pr.reconcile(apply=False)
    assert rep["ledger_match"] is False          # 원장에 없는 사다리는 불일치
