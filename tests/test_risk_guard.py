"""포지션 손실 하드캡 + 이벤트 리스크 — NVDA -13.91% 단일 손실 재발방지."""
from datetime import datetime

import pytest

from core import risk_guard


def bracket(entry=173.30, qty=1, locked=False, source="DIP_SWING_DAILY"):
    b = {"bought_qty": qty, "entry_price": entry, "sl": round(entry * 0.9, 2),
         "closed": False, "entry_source": source}
    if locked:
        b.update({"levels_locked": True, "tp1": 173.0, "sl": 150.0})
    return b


def test_hard_cap_sells_at_eight_percent():
    """NVDA 사고 재현: -8%에서 잘렸어야 한다(실제 -13.91%까지 열려 있었다)."""
    chk = risk_guard.check_position("NVDA", bracket(), 173.30 * 0.92, equity=878.0)
    assert chk["action"] == "SELL_ALL"
    assert "하드캡" in chk["reason"]


def test_no_sell_below_equity_cap_when_usd_cap_binds_first():
    """소액 계좌: -$25(자산 2% = $17.6)이 먼저 닿으면 그 지점에서 자른다."""
    chk = risk_guard.check_position("SOXL", bracket(entry=50.0, qty=2), 43.5, equity=878.0)
    assert chk["action"] == "SELL_ALL"
    assert chk["cap_usd"] == pytest.approx(17.56)


def test_locked_position_alerts_not_sold():
    chk = risk_guard.check_position("NVDA", bracket(locked=True), 160.0, equity=878.0)
    assert chk["action"] == "ALERT"
    assert chk["locked"] is True
    assert "잠금" in chk["reason"]


def test_locked_position_disaster_cap_still_sells():
    chk = risk_guard.check_position("NVDA", bracket(locked=True), 145.0, equity=878.0)
    assert chk["action"] == "SELL_ALL"
    assert "재난 캡" in chk["reason"]


def test_warn_near_cap():
    chk = risk_guard.check_position("SOXL", bracket(entry=50.0, qty=2), 46.5, equity=878.0)
    assert chk["action"] == "WARN" and "80%" in chk["reason"]


def test_normal_position_none():
    chk = risk_guard.check_position("SOXL", bracket(entry=50.0, qty=2), 49.8, equity=878.0)
    assert chk["action"] == "NONE"


def test_settings_stricter_only():
    loose = {"risk_guardrails": {"loss_cap": {"max_position_loss_pct": 25.0,
                                              "max_position_loss_usd": 90.0,
                                              "event_blackout_hours_before": 1.0}}}
    cfg = risk_guard.settings(loose)
    assert cfg["max_position_loss_pct"] <= 8.0
    assert cfg["max_position_loss_usd"] <= 25.0
    assert cfg["event_blackout_hours_before"] >= 24.0


def test_tighter_config_is_accepted():
    tighter = {"risk_guardrails": {"loss_cap": {"max_position_loss_pct": 5.0}}}
    assert risk_guard.settings(tighter)["max_position_loss_pct"] == pytest.approx(5.0)


def test_event_risk_blocks_inside_window():
    cal = {"entries": {"AVGO": {"report_at_kst": "2026-12-10 06:00"}}}
    ev = risk_guard.event_risk("AVGO", now=datetime(2026, 12, 9, 12, 0), calendar=cal)
    assert ev["block_entry"] is True
    assert ev["hours_to_event"] == pytest.approx(18.0)
    far = risk_guard.event_risk("AVGO", now=datetime(2026, 12, 1, 12, 0), calendar=cal)
    assert far["block_entry"] is False


def test_event_risk_after_report_window():
    cal = {"entries": {"NVDA": {"report_at_kst": "2026-11-18 06:20"}}}
    # 발표 3시간 뒤 — 반응일 변동성 구간(창 6시간 이내)
    ev = risk_guard.event_risk("NVDA", now=datetime(2026, 11, 18, 9, 20), calendar=cal)
    assert ev["block_entry"] is True and ev["hours_to_event"] < 0
    # 창을 벗어난 다음 날
    ev2 = risk_guard.event_risk("NVDA", now=datetime(2026, 11, 19, 12, 0), calendar=cal)
    assert ev2["block_entry"] is False


def test_cap_sl_pct_clamps():
    out = risk_guard.cap_sl_pct(15.0, equity=878.0, price=50.0, qty=2)
    assert out["sl_pct"] == pytest.approx(8.0) and out["capped"] is True
    assert out["loss_at_cap_usd"] == pytest.approx(8.0)


def test_portfolio_alerts_marks_sell_all():
    brackets = {"NVDA": bracket(), "SOXL": bracket(entry=50.0, qty=2)}
    out = risk_guard.portfolio_alerts(brackets, {"NVDA": 150.0, "SOXL": 49.8}, 878.0)
    assert "NVDA" in out["sell_all"]
