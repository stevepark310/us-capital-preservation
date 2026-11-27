"""공통 픽스처 — 실전 데이터 파일을 건드리지 않도록 임시 디렉터리로 갈아끼운다."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def kst_now(offset_min: float = 0) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9, minutes=offset_min)


def clock_at(hhmm: str, session_date: str = "2026-11-17", phase: str = "REGULAR"):
    """특정 KST 시각의 clock 딕셔너리."""
    date = "2026-11-17" if hhmm >= "12:00" else "2026-11-18"
    return {"now_kst": f"{date} {hhmm}:00", "phase": phase, "session_date": session_date,
            "next_open_kst": "2026-11-17 23:30 KST", "label_ko": "정규장 운영 중"}


@pytest.fixture(autouse=True)
def _no_real_status_files(tmp_path, monkeypatch):
    """테스트가 실전 상태 파일을 덮어쓰지 않게 한다.

    2026-11-27 실측: 전체 pytest가 `data/exit_failsafe.json`을 테스트용 시계(11-23 세션)로
    덮어써, 워치독·대시보드가 보는 '강제청산 페일세이프' 상태가 실제와 달라졌다.
    상태 파일은 tmp로 돌린다(값이 필요한 테스트는 자기 monkeypatch로 다시 지정한다).
    """
    from core import exit_failsafe
    monkeypatch.setattr(exit_failsafe, "OUT_PATH", str(tmp_path / "exit_failsafe.json"))
    yield


@pytest.fixture()
def tmp_trader(tmp_path, monkeypatch):
    """AutonomousTrader가 쓰는 파일 경로를 전부 tmp_path로 돌린다."""
    from core import autotrader, strategy_evolver
    files = {
        "STATE_PATH": "autotrader_state.json",
        "STATUS_PATH": "autotrader_status.json",
        "LOG_PATH": "autotrader_log.jsonl",
        "LEASE_PATH": "autotrader_lease.json",
        "TRADE_LEDGER": "strategy_trade_ledger.json",
        "DATA": "",
    }
    for attr, name in files.items():
        path = str(tmp_path / name) if name else str(tmp_path)
        monkeypatch.setattr(autotrader, attr, path)
    monkeypatch.setattr(strategy_evolver, "PARAMS_PATH", str(tmp_path / "strategy_params.json"))
    monkeypatch.setattr(strategy_evolver, "LOG_PATH", str(tmp_path / "strategy_evolution_log.json"))
    monkeypatch.setattr(strategy_evolver, "LEDGER_PATH", str(tmp_path / "strategy_trade_ledger.json"))
    # 개장 청산 지시(config/open_exit_plan.json)도 실제 파일을 읽지 않게 돌린다.
    from core import open_exit
    monkeypatch.setattr(open_exit, "CONFIG_PATH", str(tmp_path / "open_exit_plan.json"))
    monkeypatch.setattr(open_exit, "STATE_PATH", str(tmp_path / "open_exit_state.json"))

    brackets_path = tmp_path / "active_brackets.json"
    brackets_path.write_text(json.dumps({
        "NVDA": {"bought_qty": 1, "entry_price": 173.3, "tp1": 173.0, "tp1_qty": 1,
                 "tp1_filled": False, "tp2": None, "tp2_qty": 0, "tp2_filled": False,
                 "sl": 150.0, "levels_locked": True, "bracket_version": "fill_anchored_v2",
                 "force_exit_kst": None, "sl_active_after_kst": None},
        "SGOV": {"bought_qty": 1, "entry_price": 100.53, "note": "CARRY_PARKING", "carry_parking": True},
    }), encoding="utf-8")
    return tmp_path, brackets_path


class FakeRouter:
    """주문 라우터 대역 — 네트워크 없이 접수/거부/잔고를 흉내낸다."""

    def __init__(self, status="SUBMITTED", account=None):
        self.dry_run = False
        self.status = status
        self.account = account or {}
        self.calls = []

    def place(self, ticker, side, qty, price, reason, session_date="", dry_run=None, extra=None,
              order_type="00"):
        self.calls.append({"ticker": ticker, "side": side, "qty": qty, "price": price,
                           "reason": reason, "dry_run": dry_run, "order_type": order_type,
                           "extra": extra})
        if dry_run:
            return {"status": "DRY_RUN", "ticker": ticker, "side": side, "qty": qty, "price": price}
        return {"status": self.status, "orr_no": 1000 + len(self.calls), "run_id": 777,
                "channel": "github_actions"}

    def account_state(self, dry_run=None, refresh_min=0.0):
        return self.account


def ledger(equity=891.63, cash=426.63):
    return {"equity_usd": equity, "cash_usd": cash}
