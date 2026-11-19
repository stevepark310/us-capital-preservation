"""클라우드 사이클 — 장외 스킵, 장중 단계 순서, 상태 파일 기록."""
import json

import pytest

from scripts import cloud_cycle as cc


REGULAR = {"phase": "REGULAR", "session_date": "2026-11-18", "now_kst": "2026-11-19 01:00:00",
           "next_open_kst": "2026-11-19 23:30 KST", "kst_shift_min": 0}
OFF = dict(REGULAR, phase="OFF_HOURS", now_kst="2026-11-19 10:49:00")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CLOUD_PATH", str(tmp_path / "cloud_status.json"))
    monkeypatch.setattr(cc, "DATA", str(tmp_path))
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
    assert names == ["원장 동기화", "유령 포지션 점검", "무인 집행", "브래킷 감시", "원장 재동기화",
                     "평단 계획 재생성", "유령 포지션 재점검", "집행 상태 최신화", "대시보드 데이터 발행"]
    auto = dict(calls)["무인 집행"]
    assert auto[-1] == "--execute"
    assert dict(calls)["브래킷 감시"][-1] == "--execute"
    # 장중 사이클은 index.html을 다시 굽지 않는다(저장소 비대화 방지)
    assert "--data-only" in dict(calls)["대시보드 데이터 발행"]
    assert out["result"] == "OK" and out["ladders"] == ["NVDA"]


def test_dry_run_flag_prevents_execute(sandbox, monkeypatch):
    tmp, calls = sandbox
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: REGULAR)
    out = cc.market_cycle(dry_run=True)
    assert out["dry_run"] is True
    assert dict(calls)["무인 집행"][-1] == "--dry-run"
    assert "--execute" not in dict(calls)["브래킷 감시"]


def test_pulse_runs_collab_and_writes_status(sandbox, monkeypatch):
    tmp, calls = sandbox
    out = cc.pulse_cycle()
    names = [c[0] for c in calls]
    assert names == ["원장 동기화", "유령 포지션 점검", "집행 상태 최신화", "가격 캐시 갱신",
                     "10인 업무보고", "협업·빌드업 사이클", "대시보드 렌더"]
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
