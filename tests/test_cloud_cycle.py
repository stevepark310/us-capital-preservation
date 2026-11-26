"""클라우드 사이클 — 장외 스킵, 장중 단계 순서, 상태 파일 기록."""
import json

import pytest

from scripts import cloud_cycle as cc


REGULAR = {"phase": "REGULAR", "session_date": "2026-11-18", "now_kst": "2026-11-19 01:00:00",
           "next_open_kst": "2026-11-19 23:30 KST", "kst_shift_min": 0}
OFF = dict(REGULAR, phase="OFF_HOURS", now_kst="2026-11-19 10:49:00")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("CUBE_CLOUD_DRY_RUN", raising=False)
    monkeypatch.setattr(cc, "CLOUD_PATH", str(tmp_path / "cloud_status.json"))
    monkeypatch.setattr(cc, "DATA", str(tmp_path))
    monkeypatch.setattr(cc, "LIVE_GATE_PATH", str(tmp_path / "live_execution_gate.json"))
    calls = []

    def fake_run(name, cmd, timeout=900):
        calls.append((name, list(cmd)))
        return {"name": name, "seconds": 0.1, "rc": 0, "ok": True, "tail": "ok"}

    monkeypatch.setattr(cc, "run", fake_run)
    return tmp_path, calls


def test_off_hours_skips_before_any_order(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: OFF)
    out = cc.market_cycle()
    assert out["result"] == "SKIPPED_OFF_HOURS"
    assert calls == []                     # 장외에는 어떤 단계도 돌지 않는다
    saved = json.loads((tmp / "cloud_status.json").read_text(encoding="utf-8"))
    assert saved["market"]["result"] == "SKIPPED_OFF_HOURS"
    assert saved["schedule"]["market_cron_utc"] == cc.MARKET_CRON


def test_regular_session_runs_all_stages_in_order(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    (tmp / "autotrader_status.json").write_text(json.dumps({"mode": "EXECUTE", "positions": {"NVDA": {}},
                                                            "exits": [], "entries": []}), encoding="utf-8")
    (tmp / "avg_anchor_plan.json").write_text(json.dumps({"ladders": [{"ticker": "NVDA"}]}), encoding="utf-8")
    out = cc.market_cycle()
    names = [c[0] for c in calls]
    assert names == ["원장 동기화", "유령 포지션 점검", "10인 업무보고", "10인 10분 펄스 누적", "10인 협업 검증", "무인 집행", "브래킷 감시(읽기 전용)", "원장 재동기화",
                     "평단 계획 재생성", "유령 포지션 재점검", "집행 상태 최신화", "성과·학습 후보 평가",
                     "청산 페일세이프 점검", "퀀트 포트폴리오 위험 감시", "대시보드 데이터 발행"]
    auto = dict(calls)["무인 집행"]
    assert auto[-1] == "--dry-run"
    assert dict(calls)["브래킷 감시(읽기 전용)"][-1] == "--dry-run"
    # 장중 사이클은 index.html을 다시 굽지 않는다(저장소 비대화 방지)
    assert "--data-only" in dict(calls)["대시보드 데이터 발행"]
    assert out["result"] == "OK" and out["ladders"] == ["NVDA"]


def test_dry_run_flag_prevents_execute(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    out = cc.market_cycle(dry_run=True)
    assert out["dry_run"] is True
    assert dict(calls)["무인 집행"][-1] == "--dry-run"
    assert "--execute" not in dict(calls)["브래킷 감시(읽기 전용)"]


def test_pulse_runs_collab_and_writes_status(sandbox, monkeypatch):
    tmp, calls = sandbox
    out = cc.pulse_cycle()
    names = [c[0] for c in calls]
    assert names == ["원장 동기화", "유령 포지션 점검", "집행 상태 최신화", "가격 캐시 갱신",
                     "선행지표 동기화(M8)",
                     "10인 업무보고", "협업·빌드업 사이클", "성과·학습 후보 평가", "퀀트 포트폴리오 위험 감시", "10인 10분 펄스 누적", "대시보드 렌더"]
    saved = json.loads((tmp / "cloud_status.json").read_text(encoding="utf-8"))
    assert saved["pulse"]["result"] == "OK"
    assert saved["last_run"]["mode"] == "pulse"
    assert out["result"] == "OK"


def test_status_blocks_merge_instead_of_overwrite(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: OFF)
    cc.market_cycle()
    cc.pulse_cycle()
    saved = json.loads((tmp / "cloud_status.json").read_text(encoding="utf-8"))
    assert saved["market"]["result"] == "SKIPPED_OFF_HOURS"
    assert saved["pulse"]["result"] == "OK"


def test_default_market_dry_run_never_calls_live_preflight(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    monkeypatch.setattr(cc, "_live_account_preflight",
                        lambda: pytest.fail("preflight must not run without explicit gate"))
    out = cc.market_cycle()
    assert out["result"] == "OK"
    assert out["dry_run"] is True
    assert all("--execute" not in cmd for _, cmd in calls)


def test_failed_initial_sync_blocks_every_order(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    monkeypatch.setattr(cc, "run",
                        lambda name, cmd, timeout=900: (calls.append((name, list(cmd))) or
                        {"name": name, "rc": 1 if name == "원장 동기화" else 0,
                         "ok": name != "원장 동기화", "tail": "failed"}))
    out = cc.market_cycle()
    assert out["result"] == "BLOCKED_DEPENDENCY"
    assert not any("autotrader.py" in " ".join(cmd) for _, cmd in calls)


def test_enabled_gate_requires_successful_nh_preflight(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    (tmp / "live_execution_gate.json").write_text('{"enabled":true,"session_date":"2026-11-18","expires_at_kst":"2099-01-01 06:00:00","max_buy_order_usd":65,"max_session_buy_usd":120,"max_session_buy_orders":2}', encoding="utf-8")
    monkeypatch.setattr(cc, "_team_preflight", lambda: {"ok": True})
    monkeypatch.setattr(cc, "_live_account_preflight",
                        lambda **kw: {"ok": False, "reason": "NH account mismatch"})
    out = cc.market_cycle()
    assert out["result"] == "BLOCKED_PREFLIGHT"
    assert out["dry_run"] is False
    assert all("--execute" not in cmd for _, cmd in calls)


def test_enabled_gate_with_preflight_runs_only_one_execution_engine(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    (tmp / "live_execution_gate.json").write_text('{"enabled":true,"session_date":"2026-11-18","expires_at_kst":"2099-01-01 06:00:00","max_buy_order_usd":65,"max_session_buy_usd":120,"max_session_buy_orders":2}', encoding="utf-8")
    monkeypatch.setattr(cc, "_team_preflight", lambda: {"ok": True})
    monkeypatch.setattr(cc, "_live_account_preflight", lambda **kw: {"ok": True})
    out = cc.market_cycle()
    assert out["result"] == "OK"
    assert dict(calls)["무인 집행"][-1] == "--execute"
    assert dict(calls)["브래킷 감시(읽기 전용)"][-1] == "--dry-run"
    assert sum("--execute" in cmd for _, cmd in calls) == 1


def test_failed_execution_blocks_followup_stages(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    def fail_execute(name, cmd, timeout=900):
        calls.append((name, list(cmd)))
        ok = name != "무인 집행"
        return {"name": name, "rc": 0 if ok else 1, "ok": ok, "tail": "test"}
    monkeypatch.setattr(cc, "run", fail_execute)
    out = cc.market_cycle()
    assert out["result"] == "BLOCKED_EXECUTION"
    assert len(calls) == 6


def test_sell_only_gate_liquidates_even_when_team_reports_are_stale(sandbox, monkeypatch):
    """매도 전용 세션 — 10인 하드락은 청산의 전제가 아니다(2026-11-23 지시)."""
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    (tmp / "live_execution_gate.json").write_text(
        '{"enabled":true,"session_date":"2026-11-18","expires_at_kst":"2099-01-01 06:00:00",'
        '"sell_only":true,"max_buy_order_usd":0,"max_session_buy_usd":0,'
        '"max_session_buy_orders":0}', encoding="utf-8")
    seen = {}

    def preflight(**kw):
        seen.update(kw)
        return {"ok": True, "nh_positions": {"SQQQ": 1}}

    monkeypatch.setattr(cc, "_team_preflight", lambda: {"ok": False, "reason": "stale",
                                                         "missing_agents": [4]})
    monkeypatch.setattr(cc, "_live_account_preflight", preflight)
    out = cc.market_cycle()
    assert out["result"] == "OK"
    assert out["live_gate_sell_only"] is True
    assert out["dry_run"] is False
    assert seen.get("sell_only") is True
    assert out["team_preflight_advisory"] == "stale"
    assert dict(calls)["무인 집행"][-1] == "--execute"


def test_partial_cycle_returns_nonzero_exit_code(sandbox, monkeypatch):
    monkeypatch.setattr(cc, "pulse_cycle", lambda: {"result": "PARTIAL"})
    assert cc.main(["pulse"]) == 1


def test_live_execution_fails_closed_on_stale_agent_reporting(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    (tmp / "live_execution_gate.json").write_text('{"enabled":true,"session_date":"2026-11-18","expires_at_kst":"2099-01-01 06:00:00","max_buy_order_usd":65,"max_session_buy_usd":120,"max_session_buy_orders":2}', encoding="utf-8")
    monkeypatch.setattr(cc, "_team_preflight",
                        lambda: {"ok": False, "reason": "stale", "missing_agents": [4]})
    monkeypatch.setattr(cc, "_live_account_preflight",
                        lambda **kw: pytest.fail("NH should not be called when team is stale"))
    out = cc.market_cycle()
    assert out["result"] == "BLOCKED_TEAM"
    assert all("--execute" not in cmd for _, cmd in calls)


def test_all_ten_agent_reports_are_required(sandbox):
    tmp, calls = sandbox
    now = cc.now_str()
    state = {str(i): {"line": "ok", "ts_kst": now} for i in range(1, 11)}
    (tmp / "agent_collab_bus.json").write_text(
        json.dumps({"agent_state": state}), encoding="utf-8")
    (tmp / "collab_cycle.json").write_text(
        json.dumps({"hard_lock": {"ok": True}, "cycle": 8}), encoding="utf-8")
    assert cc._team_preflight()["ok"] is True
    state["6"]["line"] = ""
    (tmp / "agent_collab_bus.json").write_text(
        json.dumps({"agent_state": state}), encoding="utf-8")
    assert cc._team_preflight()["missing_agents"] == [6]


def test_pulse_main_triggers_market_cycle_during_regular_session(sandbox, monkeypatch):
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    ran = []
    monkeypatch.setattr(cc, "pulse_cycle", lambda: (ran.append("pulse"), {"result": "OK"})[1])
    monkeypatch.setattr(cc, "market_cycle", lambda: (ran.append("market"), {"result": "OK"})[1])
    assert cc.main(["pulse"]) == 0
    assert ran == ["pulse", "market"]


def test_pulse_main_skips_market_cycle_when_off_hours(sandbox, monkeypatch):
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: OFF)
    ran = []
    monkeypatch.setattr(cc, "pulse_cycle", lambda: (ran.append("pulse"), {"result": "OK"})[1])
    monkeypatch.setattr(cc, "market_cycle", lambda: (ran.append("market"), {"result": "OK"})[1])
    assert cc.main(["pulse"]) == 0
    assert ran == ["pulse"]
