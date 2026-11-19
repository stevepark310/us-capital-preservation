"""집행 상태 파일 최신화 — 시계·phase·보유·예산이 '지금' 값으로 다시 쓰이는가."""
import json

import pytest

from core import autotrader as at_mod


CLOCK = {"now_kst": "2026-11-19 10:49:10", "phase": "OFF_HOURS", "session_date": "2026-11-18",
         "next_open_kst": "2026-11-19 23:30 KST", "label_ko": "휴장 시간대",
         "session_times_kst": {"regular_open": "23:30", "regular_close": "06:00"},
         "kst_shift_min": 0, "hours_to_next_open": 12.7}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(at_mod, "DATA", str(tmp_path))
    monkeypatch.setattr(at_mod, "STATUS_PATH", str(tmp_path / "autotrader_status.json"))
    monkeypatch.setattr(at_mod, "STATE_PATH", str(tmp_path / "autotrader_state.json"))
    monkeypatch.setattr(at_mod.market_clock, "resolve", lambda *a, **k: CLOCK)
    monkeypatch.setattr(at_mod.AutonomousTrader, "_ledger", lambda self: {"equity_usd": 878.6, "cash_usd": 426.63})
    monkeypatch.setattr("core.ledger.summary", lambda snapshot=None: {"equity_usd": 878.6, "cash_usd": 426.63})
    (tmp_path / "active_brackets.json").write_text(json.dumps({
        "NVDA": {"entry_price": 173.3, "bought_qty": 1, "sl": 150.0, "tp1": 173.0,
                 "levels_locked": True, "closed": False},
        "SOXL": {"entry_price": 50.43, "bought_qty": 2, "sl": 49.17, "closed": True},
    }), encoding="utf-8")
    return tmp_path


def test_status_is_written_with_current_clock_and_positions(env):
    st = at_mod.refresh_status(by="test")
    saved = json.loads((env / "autotrader_status.json").read_text(encoding="utf-8"))
    assert saved["generated_at_kst"] == st["generated_at_kst"]
    assert saved["generated_at_kst"] >= "2026-11-19 10:49"
    assert saved["phase"] == "OFF_HOURS"
    assert saved["session_date"] == "2026-11-18"
    assert list(saved["positions"]) == ["NVDA"]            # 청산된 SOXL은 보유가 아니다
    assert saved["next_gate"].startswith("다음 정규장")
    assert saved["status_sync"]["by"] == "test"


def test_previous_execution_record_is_preserved(env):
    (env / "autotrader_status.json").write_text(json.dumps({
        "generated_at_kst": "2026-11-18 14:32:54", "mode": "EXECUTE",
        "exits": [{"ticker": "SOXL", "qty": 2}], "entries": [{"ticker": "TQQQ"}],
        "evolution": {"decision": "NO_CHANGE"},
    }), encoding="utf-8")
    st = at_mod.refresh_status(by="test")
    assert st["mode"] == "EXECUTE"                       # 마지막 집행 기록을 지우지 않는다
    assert st["exits"] and st["entries"]
    assert st["evolution"]["decision"] == "NO_CHANGE"
    assert st["status_sync"]["previous_generated_at_kst"] == "2026-11-18 14:32:54"


def test_daily_counters_reset_when_session_rolls(env):
    (env / "autotrader_state.json").write_text(json.dumps({
        "session_date": "2026-11-17", "entries": [{"ticker": "SOXL"}], "orders": [{"ticker": "SOXL"}],
        "realized_pnl_usd": -8.43, "halted": True, "pending": [],
    }), encoding="utf-8")
    st = at_mod.refresh_status(by="test")
    assert st["entries_today"] == 0 and st["orders_today"] == 0
    assert st["realized_pnl_today_usd"] == 0.0
    assert st["halted"] is False


def test_guardrails_are_reported(env):
    st = at_mod.refresh_status(by="test")
    g = st["policy"]["guardrails"]
    assert g["cash_reserve_floor_usd"] == 150.0
    assert g["daily_realized_loss_limit_usd"] == 25.0
    assert g["nvda_levels_locked"]["tp"] == 173.0 and g["nvda_levels_locked"]["sl"] == 150.0
    assert st["policy"]["ask_before_trade"] is False
    assert st["policy"]["entry_budget"]["budget_usd"] > 0
