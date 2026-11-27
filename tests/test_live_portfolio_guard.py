"""live_portfolio 동기화의 방어 로직 — 원장 읽기 실패가 실측 보유를 지우지 못한다.

2026-11-28 04:02 KST 실사고: `data/live_portfolio.json` 읽기가 한 번 실패하자 동기화가
`ledger.summary()`의 빈 기본값을 정본에 덮어써 SGOV 1주(실측 $100.68)가 사라졌고,
그 뒤 모든 화면·리컨실리에이션이 `holdings {}`로 계산됐다. 여기서는 같은 경로를 재현해
① 읽기 실패 시 쓰기 중단 ② 체결 근거 없는 보유 소실 시 쓰기 중단 ③ 사본(docs) 복구
④ 이미 확인된 account_sync·cash_deposit 보존 — 네 가지를 고정한다.
"""
import importlib
import json

import pytest

sync_mod = importlib.import_module("scripts.sync_live_portfolio")


def ledger_summary(positions, **over):
    base = {
        "account_no": "202-05-134849", "ledger_type": "REAL_TRADING_LIVE_LEDGER",
        "fx_rate": 1342.7, "fx_source": "실전 원장 잔고 역산", "positions": positions,
        "equity_usd": 881.27, "equity_krw": 1183280,
        "cash_usd": 780.56, "cash_krw": 1048057,
        "cash_pending_settlement_usd": 0.0, "cash_pending_settlement_krw": 0,
        "cash_deposit_usd": None, "cash_deposit_krw": None,
        "account_sync": None, "fills_confirmed": None,
        "realized_pnl_usd": -17.43, "realized_pnl_krw": -23403,
        "unrealized_pnl_usd": 0.18, "unrealized_pnl_krw": 181,
        "net_pnl_usd": -17.25, "win_rate_pct": 54.5,
        "trades": 11, "wins": 6, "losses": 5, "recent_trades": [],
        "initial_funding_krw": 1209215, "initial_funding_usd": 900.18,
        "cash_pct": 88.6, "last_synced": "2026-11-27 08:00:00 KST",
    }
    base.update(over)
    return base


PREV = {
    "last_synced": "2026-11-27 08:00:00 KST",
    "current_total_equity_usd": 881.27,
    "cash_buffer_usd": 780.56,
    "cash_deposit_usd": 780.56,
    "account_sync": {"source": "NH Open API", "workflow_run_id": 46082643734},
    "fills_confirmed": [],
    "positions": [{"ticker": "SGOV", "qty": 1, "avg_price": 100.53, "market_price": 100.71}],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    live = tmp_path / "live_portfolio.json"
    mirror = tmp_path / "docs_live_portfolio.json"
    snap = tmp_path / "nh_account_snapshot.json"
    monkeypatch.setattr(sync_mod, "LIVE_PATH", str(live))
    monkeypatch.setattr(sync_mod, "LIVE_MIRROR", str(mirror))
    monkeypatch.setattr(sync_mod, "SNAPSHOT_PATH", str(snap))
    monkeypatch.setattr(sync_mod.market_snapshot, "build_snapshot",
                        lambda **kw: {"clock": {"phase": "POST_MARKET"}, "quotes": {}})
    monkeypatch.setattr(sync_mod.ledger_mod, "validate",
                        lambda **kw: {"checked_at_kst": "test", "status": "CONSISTENT", "issue_count": 0})
    monkeypatch.setattr(sync_mod.ledger_mod, "load_calendar", lambda: {"daily_records": {}})
    return live, mirror, snap


def test_unreadable_ledger_never_overwrites(env):
    live, _mirror, _snap = env
    live.write_text('{"positions": [{"ticker": "SGOV"', encoding="utf-8")   # 반쯤 쓰인 JSON
    before = live.read_text(encoding="utf-8")
    with pytest.raises(sync_mod.LedgerReadError):
        sync_mod.sync_portfolio()
    assert live.read_text(encoding="utf-8") == before          # 원본 그대로


def test_empty_summary_cannot_blank_positions(env, monkeypatch):
    live, _mirror, _snap = env
    live.write_text(json.dumps(PREV), encoding="utf-8")
    # 파일은 읽히지만 요약이 빈 포지션을 돌려주는 경우(읽기 실패 폴백과 같은 결과)
    monkeypatch.setattr(sync_mod.ledger_mod, "summary", lambda snapshot=None: ledger_summary([]))
    with pytest.raises(sync_mod.PositionLossError):
        sync_mod.sync_portfolio()
    assert json.loads(live.read_text(encoding="utf-8"))["positions"][0]["ticker"] == "SGOV"


def test_mirror_restores_corrupt_ledger(env, monkeypatch):
    live, mirror, _snap = env
    mirror.write_text(json.dumps(PREV), encoding="utf-8")
    live.write_text("", encoding="utf-8")
    monkeypatch.setattr(sync_mod.ledger_mod, "summary",
                        lambda snapshot=None: ledger_summary(PREV["positions"]))
    payload = sync_mod.sync_portfolio()
    assert payload["positions"][0]["ticker"] == "SGOV"
    assert json.loads(live.read_text(encoding="utf-8"))["positions"][0]["ticker"] == "SGOV"


def test_confirmed_values_are_carried_forward(env, monkeypatch):
    live, _mirror, _snap = env
    live.write_text(json.dumps(PREV), encoding="utf-8")
    monkeypatch.setattr(sync_mod.ledger_mod, "summary",
                        lambda snapshot=None: ledger_summary(PREV["positions"]))
    payload = sync_mod.sync_portfolio()
    # 요약이 null을 돌려줘도 이미 확인된 값은 지우지 않는다.
    assert payload["account_sync"]["workflow_run_id"] == 46082643734
    assert payload["fills_confirmed"] == []
    assert payload["cash_deposit_usd"] == 780.56


def test_position_loss_is_allowed_when_a_sell_is_recorded(env, monkeypatch):
    live, _mirror, _snap = env
    live.write_text(json.dumps(PREV), encoding="utf-8")
    monkeypatch.setattr(sync_mod.ledger_mod, "summary", lambda snapshot=None: ledger_summary([]))
    monkeypatch.setattr(sync_mod.ledger_mod, "load_calendar", lambda: {"daily_records": {
        "2026-11-27": {"trades": [{"ticker": "SGOV", "side": "SELL", "qty": 1, "price": 100.71}]}}})
    payload = sync_mod.sync_portfolio()
    assert payload["positions"] == []                          # 정당한 청산은 반영된다
