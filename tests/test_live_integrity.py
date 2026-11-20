"""실시간 VIX/시세 정합성 — 2026-11-19 사고(VIX 27.x로 28+ 구간 통과) 재발방지."""
from datetime import datetime, timedelta, timezone

import pytest

from core import live_integrity


def fresh_bar(minutes_ago=2):
    """지금 기준 신선한 5분봉 시각(테스트가 실행 시각에 의존하지 않도록)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def clock(hhmm="01:00", phase="REGULAR", session="2026-11-19", shift=0):
    return {"now_kst": f"2026-11-20 {hhmm}:00", "phase": phase, "session_date": session,
            "kst_shift_min": shift, "session_times_kst": {"regular_close": "06:00"}}


def snap(vix_last=27.14, day_high=28.99, bar_date="2026-11-19", quotes=None, age_min=0):
    from datetime import datetime, timedelta, timezone
    gen = (datetime.now(timezone.utc) - timedelta(minutes=age_min)).isoformat()
    return {"generated_at_utc": gen,
            "macro": {"vix": {"last": vix_last, "prev_close": 24.56, "day_high": day_high,
                              "day_low": 21.84, "last_bar_date": bar_date}},
            "quotes": quotes or {}}


def test_session_high_gates_even_when_close_is_below_28():
    """11-19 실측: 종가 27.14 · 세션 고가 28.99 → 게이트는 28.99 (신규 롱 차단)."""
    out = live_integrity.evaluate(snap(), clock(), tickers=["SOXL"])
    assert out["vix"]["gate"] == pytest.approx(28.99)
    assert out["block_new_long"] is True
    assert any("세션 중 VIX 고가" in r for r in out["vix"]["reasons"])


def test_vix_28_floor_blocks_long_regardless_of_close():
    out = live_integrity.evaluate(snap(vix_last=27.99, day_high=27.99), clock(), tickers=[])
    assert out["block_new_long"] is False
    out2 = live_integrity.evaluate(snap(vix_last=28.0, day_high=28.0), clock(), tickers=[])
    assert out2["block_new_long"] is True


def test_stale_vix_bar_fails_closed():
    out = live_integrity.evaluate(snap(bar_date="2026-11-18"), clock(), tickers=[])
    assert out["vix"]["stale"] is True
    assert out["vix"]["gate"] == pytest.approx(99.0)
    assert out["block_new_long"] is True


def test_stale_snapshot_fails_closed():
    out = live_integrity.evaluate(snap(), clock(), tickers=[], )
    assert out["snapshot_stale"] is False
    out2 = live_integrity.evaluate(snap(age_min=30), clock(), tickers=[])
    assert out2["snapshot_stale"] is True and out2["block_new_long"] is True


def test_cross_source_divergence_reported_and_raised():
    out = live_integrity.evaluate(snap(vix_last=27.14, day_high=27.14), clock(),
                                  alt_vix=28.44, tickers=[])
    assert out["vix"]["divergence"] == pytest.approx(1.3)
    assert out["vix"]["gate"] == pytest.approx(28.44)
    assert out["block_new_long"] is True


def test_price_for_sell_is_conservative():
    quotes = {"SOXL": {"intraday_price": 47.40, "last": 46.47, "intraday_bar_time":
                       "2026-11-19 15:55:00-05:00", "prev_close": 49.26}}
    view = live_integrity.price_for(quotes, "SOXL", clock("03:32"), "SELL")
    assert view["conservative"] == pytest.approx(46.47)
    assert view["side_price"] == pytest.approx(46.47)
    buy = live_integrity.price_for(quotes, "SOXL", clock("03:32"), "BUY")
    assert buy["side_price"] == pytest.approx(47.40)


def test_price_stale_flags_untrusted():
    quotes = {"SOXL": {"intraday_price": 47.4, "last": 46.47,
                       "intraday_bar_time": "2026-11-19 09:35:00-05:00"}}
    view = live_integrity.price_view(quotes, "SOXL", clock("05:00"))
    assert view["stale"] is True and view["trusted"] is False
    assert any("지연" in r for r in view["reasons"])


def test_broker_divergence_untrusted():
    quotes = {"SOXL": {"intraday_price": 47.40, "last": 47.40,
                       "intraday_bar_time": "2026-11-19 15:55:00-05:00"}}
    view = live_integrity.price_view(quotes, "SOXL", clock("03:32"), broker_mark=49.0)
    assert view["divergence_pct"] > 1.0
    assert view["trusted"] is False


def test_entry_gate_messages():
    integ = live_integrity.evaluate(snap(), clock(), tickers=["SOXL"])
    assert "VIX 정합성" in live_integrity.entry_gate(integ, "SOXL")
    healthy = live_integrity.evaluate(
        snap(vix_last=18.0, day_high=18.5,
             quotes={"SOXL": {"intraday_price": 50.0, "last": 50.2,
                              "intraday_bar_time": fresh_bar(),
                              "prev_close": 49.8}}),
        clock(), tickers=["SOXL"])
    assert live_integrity.entry_gate(healthy, "SOXL") is None


def test_hard_limits_cannot_be_loosened_by_config():
    loose = {"risk_guardrails": {"live_integrity": {"max_snapshot_age_min": 60.0,
                                                    "vix_new_long_block": 40.0,
                                                    "max_intraday_bar_age_min": 300.0}}}
    cfg = live_integrity.settings(loose)
    assert cfg["max_snapshot_age_min"] <= live_integrity.HARD_LIMITS["max_snapshot_age_min"]
    assert cfg["vix_new_long_block"] <= live_integrity.HARD_LIMITS["vix_new_long_block"]
    assert cfg["max_intraday_bar_age_min"] <= live_integrity.HARD_LIMITS["max_intraday_bar_age_min"]
