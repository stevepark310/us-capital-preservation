"""주문 브리지 — 워크플로우 스펙·로그 파싱·중복 차단·디스패치 경로 (네트워크 없음)."""
import json

import pytest

from execution import order_bridge as ob


LOG_BUY = """execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3747836Z Token issued: True
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3752396Z {
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3755203Z   "Output_0": {
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3759000Z     "orr_no": 249712
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3760000Z   },
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3761000Z   "rsp_cd": "00171",
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3762000Z   "rsp_msg": "주문이 완료되었습니다."
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3763000Z }
"""

LOG_ACCOUNT = """execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3747836Z Token issued: True
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3752396Z {
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3755203Z   "equity": 892.92,
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3756000Z   "cash_usd": 636.01,
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3757000Z   "positions": {"SOXL": {"qty": 2, "avg_price": 50.43, "sellable_qty": 2}},
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3761000Z   "rsp_cd": "00166",
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3762000Z   "rsp_msg": "조회가 완료되었습니다."
execute-nh-trade\tRun NH Trade / Monitor\t2026-11-17T02:15:13.3763000Z }
"""


def test_build_command_matches_workflow_inputs():
    r = ob.OrderRouter(repo="stevepark310/us-capital-preservation", dry_run=True)
    cmd = r.build_command("buy", "SOXL", 1, 50.6)
    assert cmd[:4] == ["gh", "workflow", "run", "nh_trade_dispatch.yml"]
    assert "-R" in cmd and "stevepark310/us-capital-preservation" in cmd
    assert "mode=buy" in cmd and "ticker=SOXL" in cmd and "qty=1" in cmd and "price=50.60" in cmd
    # 워크플로우는 place_order 모드가 없다 — buy/sell/test_balance/monitor만 지원
    assert "mode=place_order" not in " ".join(cmd)


def test_parse_order_response_accepts_order():
    parsed = ob.parse_order_response(LOG_BUY)
    assert parsed["found"] is True
    assert parsed["status"] == "SUBMITTED"
    assert parsed["orr_no"] == 249712
    assert parsed["rsp_cd"] == "00171"


def test_parse_order_response_rejects_unknown():
    log = LOG_BUY.replace("00171", "16156")
    parsed = ob.parse_order_response(log)
    assert parsed["status"] == "REJECTED"


def test_parse_account_state():
    parsed = ob.parse_account_state(LOG_ACCOUNT)
    assert parsed["found"] is True
    assert parsed["equity_usd"] == 892.92
    assert parsed["positions"]["SOXL"]["qty"] == 2


def test_parse_missing_json_is_reported():
    assert ob.parse_order_response("nothing here")["status"] == "UNPARSED"
    assert ob.parse_account_state("nothing")["found"] is False


class Runner:
    """gh 호출 대역 — 디스패치 후 새 런이 생기고 완료되는 흐름을 흉내낸다."""

    def __init__(self, log=LOG_BUY, fail_dispatch=False):
        self.log = log
        self.fail_dispatch = fail_dispatch
        self.commands = []
        self.dispatched = False

    def __call__(self, args, timeout=120):
        self.commands.append(args)
        if args[:3] == ["gh", "workflow", "run"]:
            if self.fail_dispatch:
                return {"rc": 1, "out": "", "err": "HTTP 403: Resource not accessible by integration"}
            self.dispatched = True
            return {"rc": 0, "out": "https://github.com/x/actions/runs/500", "err": ""}
        if args[:3] == ["gh", "run", "list"]:
            runs = [{"databaseId": 499, "status": "completed", "conclusion": "success"}]
            if self.dispatched:
                runs.append({"databaseId": 500, "status": "completed", "conclusion": "success"})
            return {"rc": 0, "out": json.dumps(runs), "err": ""}
        if args[:3] == ["gh", "run", "view"] and "--log" in args:
            return {"rc": 0, "out": self.log, "err": ""}
        if args[:3] == ["gh", "run", "view"]:
            return {"rc": 0, "out": json.dumps({"status": "completed", "conclusion": "success"}), "err": ""}
        return {"rc": 0, "out": "", "err": ""}


def fast_router(**kw):
    return ob.OrderRouter(dry_run=kw.pop("dry_run", False), run_cmd=kw.pop("run_cmd"),
                          poll_first_s=0.01, poll_interval_s=0.01, status_poll_s=0.01, **kw)


def test_place_dispatches_and_parses(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "dispatch.json"))
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "orders.jsonl"))
    runner = Runner()
    r = fast_router(run_cmd=runner)
    res = r.place("SOXL", "BUY", 1, 50.60, "test", session_date="2026-11-17", dry_run=False)
    assert res["status"] == "SUBMITTED"
    assert res["orr_no"] == 249712
    assert res["channel"] == "github_actions"
    assert any("mode=buy" in c for c in runner.commands if isinstance(c, list))
    # 로그 1건이 남는다
    lines = open(ob.ORDERS_LOG, encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["status"] == "SUBMITTED"


def test_duplicate_order_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "dispatch.json"))
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "orders.jsonl"))
    runner = Runner()
    r = fast_router(run_cmd=runner)
    first = r.place("SOXL", "BUY", 1, 50.60, "test", session_date="2026-11-17", dry_run=False)
    second = r.place("SOXL", "BUY", 1, 50.60, "test", session_date="2026-11-17", dry_run=False)
    assert first["status"] == "SUBMITTED"
    assert second["status"] == "DUPLICATE_BLOCKED"
    dispatch_calls = [c for c in runner.commands if c[:3] == ["gh", "workflow", "run"]]
    assert len(dispatch_calls) == 1


def test_dispatch_failure_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "dispatch.json"))
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "orders.jsonl"))
    r = fast_router(run_cmd=Runner(fail_dispatch=True))
    res = r.place("SOXL", "SELL", 1, 49.5, "test", session_date="2026-11-17", dry_run=False)
    assert res["status"] == "DISPATCH_FAILED"
    assert "403" in res["error"]


def test_dry_run_never_calls_gh(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "dispatch.json"))
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "orders.jsonl"))
    runner = Runner()
    r = ob.OrderRouter(dry_run=True, run_cmd=runner)
    res = r.place("SOXL", "BUY", 1, 50.6, "test", session_date="2026-11-17")
    assert res["status"] == "DRY_RUN"
    assert runner.commands == []


def test_invalid_qty_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "orders.jsonl"))
    r = ob.OrderRouter(dry_run=True)
    assert r.place("SOXL", "BUY", 0, 50.0, "x")["status"] == "INVALID"


def test_account_state_parsed_from_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "ACCOUNT_SNAPSHOT", str(tmp_path / "acct.json"))
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "dispatch.json"))
    r = fast_router(run_cmd=Runner(log=LOG_ACCOUNT))
    state = r.account_state()
    assert state["found"] is True
    assert state["cash_usd"] == 636.01
    assert state["positions"]["SOXL"]["avg_price"] == 50.43


def test_cloud_pretrade_blocks_insufficient_cash(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "LIVE_GATE_PATH", str(tmp_path / "gate.json"))
    monkeypatch.setattr(ob, "BASE_DIR", str(tmp_path))
    (tmp_path / "gate.json").write_text('{"enabled":true}', encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "live_portfolio.json").write_text(
        '{"cash_deposit_usd":160}', encoding="utf-8")
    from execution import nh_trader
    monkeypatch.setattr(nh_trader, "NHBroker", lambda dry_run=False: type("FakeNH", (), {
        "get_portfolio_state": lambda self, include_raw=False: {
            "rsp_cd": "00166", "equity": 300, "cash": 200, "positions": {}}})())
    result = ob.OrderRouter(dry_run=False)._live_pretrade_check("SOXL", "BUY", 1, 40)
    assert result["ok"] is False
    assert "cash" in result["reason"]


def test_cloud_pretrade_blocks_unsellable_shares(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "LIVE_GATE_PATH", str(tmp_path / "gate.json"))
    (tmp_path / "gate.json").write_text('{"enabled":true}', encoding="utf-8")
    from execution import nh_trader
    monkeypatch.setattr(nh_trader, "NHBroker", lambda dry_run=False: type("FakeNH", (), {
        "get_portfolio_state": lambda self, include_raw=False: {
            "rsp_cd": "00166", "equity": 300, "cash": 20,
            "positions": {"NVDA": {"qty": 2, "sellable_qty": 1}}}})())
    result = ob.OrderRouter(dry_run=False)._live_pretrade_check("NVDA", "SELL", 2, 150)
    assert result["ok"] is False
    assert result["sellable_qty"] == 1


def test_cloud_pretrade_blocks_failed_nh_response(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "LIVE_GATE_PATH", str(tmp_path / "gate.json"))
    (tmp_path / "gate.json").write_text('{"enabled":true}', encoding="utf-8")
    from execution import nh_trader
    monkeypatch.setattr(nh_trader, "NHBroker", lambda dry_run=False: type("FakeNH", (), {
        "get_portfolio_state": lambda self, include_raw=False: {
            "rsp_cd": "ERROR", "equity": 300, "cash": 200, "positions": {}}})())
    assert ob.OrderRouter(dry_run=False)._live_pretrade_check("SOXL", "BUY", 1, 40)["ok"] is False


def test_cloud_route_blocks_live_order_without_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(ob, "LIVE_GATE_PATH", str(tmp_path / "no-gate.json"))
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "dispatch.json"))
    runner = Runner()
    result = fast_router(run_cmd=runner).place("SOXL", "BUY", 1, 50, "test", dry_run=False)
    assert result["status"] == "BLOCKED_PREFLIGHT"
    assert not runner.commands


def test_ambiguous_direct_outcome_does_not_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "dispatch.json"))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    runner = Runner()
    router = fast_router(run_cmd=runner)
    monkeypatch.setattr(router, "_try_direct", lambda *a: {
        "status": "UNKNOWN_NEEDS_RECONCILE", "error": "TimeoutError"})
    result = router.place("SOXL", "BUY", 1, 50, "test", dry_run=False)
    assert result["status"] == "UNKNOWN_NEEDS_RECONCILE"
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in runner.commands)
