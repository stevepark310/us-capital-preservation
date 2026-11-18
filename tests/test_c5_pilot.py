"""C5 파일럿 — 개장 30분 방향 1일 1회 (2026-11-18 11:37 사용자 승인).

검증 항목: 블랙아웃(오늘 11-18 NVDA 반응일) · 방향 판정 · 1일 1회 · 진입 창 ·
티커 충돌 회피 · 브래킷 규격(손절 3×ATR · 05:40 청산) · C5와 반대 방향 차단.
"""
import json

import pandas as pd
import pytest

from core import autotrader, entry_signals, exit_rules
from core.autotrader import AutonomousTrader

from conftest import FakeRouter, clock_at, ledger


def make_market(clock, quotes=None, vix=22.0, daily=None):
    return {"clock": clock, "quotes": quotes or {}, "techs": {}, "daily": daily or {},
            "snapshot": {}, "vix": vix}


def soxl_daily(prev_close=59.0, last="2026-11-14"):
    idx = pd.to_datetime([last])
    return {"SOXL": pd.DataFrame({"Close": [prev_close]}, index=idx)}


@pytest.fixture()
def trader(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    t = AutonomousTrader(policy=autotrader.execution_policy.load_policy(), router=FakeRouter(account={"found": False}),
                         dry_run=False, market=make_market(clock_at("00:05", session_date="2026-11-16")),
                         base_dir=str(tmp_path))
    t._atr_from_bars = lambda tk: 2.5          # 네트워크 없이 장중 ATR 주입
    return t


# ------------------------------------------------------------------ blackout
def test_blackout_today_is_nvda_reaction_day():
    today = entry_signals.c5_blackout("2026-11-18")
    assert today["blackout"] is True
    assert today["explicit"] is True
    assert today["earnings_reaction"] == "NVDA"
    assert "실적 반응일" in today["reason"]


def test_blackout_clear_on_normal_sessions():
    for d in ("2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17"):
        assert entry_signals.c5_blackout(d)["blackout"] is False


def test_earnings_reaction_dates_cover_calendar():
    """달력의 모든 실적일은 '반응 세션'을 하나씩 남긴다(NVDA → 11-18)."""
    dates = entry_signals.earnings_reaction_dates()
    assert dates.get("2026-11-18") == "NVDA"
    assert dates.get("2026-12-10") == "AVGO"       # 12-10 06:00 KST = 12-09 16:00 ET → 다음 거래일


# ------------------------------------------------------------------ direction
def test_direction_from_open30_vs_prev_close():
    up = entry_signals.c5_direction(59.0, 60.0)
    assert up["direction"] == "LONG" and up["gap_pct"] == pytest.approx(1.695, abs=0.01)
    dn = entry_signals.c5_direction(59.0, 58.0)
    assert dn["direction"] == "SHORT"
    flat = entry_signals.c5_direction(59.0, 59.0)
    assert flat["direction"] == "SHORT"            # 연구 시뮬레이터와 같은 규칙(> 만 LONG)
    assert entry_signals.c5_direction(None, 59.0)["direction"] is None


# ------------------------------------------------------------------ candidate
def test_c5_candidate_short_with_3atr_stop(trader):
    market = make_market(clock_at("00:05", session_date="2026-11-16"),
                         {"SOXL": {"intraday_price": 58.0}}, daily=soxl_daily(59.0))
    res = trader._c5_candidate(market, {"entries": [], "pending": []}, {})
    assert res["armed"] is True
    cand = res["candidate"]
    assert cand["source"] == "C5_OPEN30_DIRECTION" and cand["ticker"] == "SOXS"
    assert cand["notional_cap"] == 150.0
    assert cand["time_exit_kst"] == "05:40"
    assert cand["signal"]["stop_pct"] == pytest.approx(7.5)     # 3 × ATR 2.5%


def test_c5_candidate_long_uses_soxl(trader):
    market = make_market(clock_at("00:05", session_date="2026-11-16"),
                         {"SOXL": {"intraday_price": 61.0}}, daily=soxl_daily(59.0))
    res = trader._c5_candidate(market, {"entries": [], "pending": []}, {})
    assert res["candidate"]["ticker"] == "SOXL"
    assert res["candidate"]["signal"]["direction"] == "LONG"


def test_c5_candidate_only_once_per_session(trader):
    st = {"entries": [{"ticker": "SOXS", "source": "C5_OPEN30_DIRECTION"}], "pending": []}
    market = make_market(clock_at("00:15", session_date="2026-11-16"),
                         {"SOXL": {"intraday_price": 58.0}}, daily=soxl_daily(59.0))
    res = trader._c5_candidate(market, st, {})
    assert res["armed"] is False and "1일 1회" in res["reason"]


def test_c5_candidate_blocked_after_window(trader):
    market = make_market(clock_at("01:30", session_date="2026-11-16"),
                         {"SOXL": {"intraday_price": 58.0}}, daily=soxl_daily(59.0))
    res = trader._c5_candidate(market, {"entries": [], "pending": []}, {})
    assert res["armed"] is False and "창 밖" in res["reason"]


def test_c5_candidate_blackout_today(trader):
    """오늘(11-18 ET) 세션은 NVDA 반응일 — SOXS 조건부 집행과 겹치지 않게 진입 금지."""
    market = make_market(clock_at("00:05", session_date="2026-11-18"),
                         {"SOXL": {"intraday_price": 58.0}}, daily=soxl_daily(59.0))
    res = trader._c5_candidate(market, {"entries": [], "pending": []}, {})
    assert res["armed"] is False
    assert "진입 제외일" in res["reason"]


def test_c5_candidate_moves_to_free_ticker(trader):
    brackets = {"SOXS": {"bought_qty": 1, "entry_price": 59.0, "closed": False}}
    market = make_market(clock_at("00:05", session_date="2026-11-16"),
                         {"SOXL": {"intraday_price": 58.0}}, daily=soxl_daily(59.0))
    res = trader._c5_candidate(market, {"entries": [], "pending": []}, brackets)
    assert res["candidate"]["ticker"] == "SQQQ"


# ------------------------------------------------------------------ bracket
def test_c5_bracket_is_time_exit_pilot_spec(trader):
    cand = {"source": "C5_OPEN30_DIRECTION", "atr_pct": 2.5, "time_exit_kst": "05:40",
            "why": "테스트"}
    b = trader._new_bracket(cand, "SOXS", 59.0, 2, None, "C5_OPEN30_DIRECTION",
                            {"trail_atr_mult": 1.5})
    assert b["bracket_class"] == "PILOT_C5"
    assert b["exit_mode"] == "time_exit"
    assert b["force_exit_kst"] == "05:40"
    assert b["initial_sl_pct"] == pytest.approx(7.5)
    assert b["sl"] == pytest.approx(54.58, abs=0.02)
    assert b.get("trail_atr_mult") is None


def test_c5_exits_on_time_exit_at_0540(trader):
    b = {"bought_qty": 2, "entry_price": 59.0, "sl": 54.58, "initial_sl_pct": 7.5,
         "exit_mode": "time_exit", "force_exit_kst": "05:40", "sl_active_after_kst": "00:00",
         "closed": False}
    early = exit_rules.decide(b, 59.5, clock_at("05:20", session_date="2026-11-16"))
    assert early is None
    late = exit_rules.decide(b, 59.5, clock_at("05:41", session_date="2026-11-16"))
    assert late["action"] == "SELL" and "05:40" in late["reason"]
    assert late["post"]["closed"] is True


def test_c5_stop_loss_fires_after_entry(trader):
    b = {"bought_qty": 2, "entry_price": 59.0, "sl": 54.58, "initial_sl_pct": 7.5,
         "exit_mode": "time_exit", "force_exit_kst": "05:40", "sl_active_after_kst": "00:00",
         "closed": False}
    d = exit_rules.decide(b, 54.0, clock_at("02:00", session_date="2026-11-16"))
    assert d["stop"] is True and d["close"] is True


# ------------------------------------------------------------------ entries
def c5_cand(ticker="SOXS"):
    return {"source": "C5_OPEN30_DIRECTION", "ticker": ticker, "atr_pct": 2.5,
            "why": "C5 SHORT — 10:00 $58.00 ≤ 전일 종가 $59.00 (-1.69%)", "pilot": "C5",
            "notional_cap": 150.0, "time_exit_kst": "05:40", "allow_opening_entry": True,
            "signal": {"direction": "SHORT", "stop_pct": 7.5}}


def test_c5_entry_places_order_within_pilot_budget(trader, tmp_trader):
    tmp_path, brackets_path = tmp_trader
    brackets = json.loads(brackets_path.read_text(encoding="utf-8"))
    st = trader.load_state("2026-11-16")
    trader.entry_candidates = lambda *a, **k: [c5_cand()]
    market = make_market(clock_at("00:05", session_date="2026-11-16"),
                         {"SOXS": {"intraday_price": 59.0}}, vix=22.0)
    actions = trader.entries(st, brackets, market, ledger())
    assert len(actions) == 1
    buy = actions[0]
    assert buy["source"] == "C5_OPEN30_DIRECTION" and buy["ticker"] == "SOXS"
    assert buy["sizing"]["notional_usd"] <= 150.0 + 0.01
    b = brackets["SOXS"]
    assert b["entry_source"] == "C5_OPEN30_DIRECTION"
    assert b["force_exit_kst"] == "05:40"
    assert b["bracket_class"] == "PILOT_C5"
    assert b.get("trail_atr_mult") is None


def test_c5_blocks_opposite_direction_hedge(trader, tmp_trader):
    tmp_path, brackets_path = tmp_trader
    brackets = json.loads(brackets_path.read_text(encoding="utf-8"))
    brackets["SOXL"] = {"bought_qty": 2, "entry_price": 58.0, "closed": False,
                        "entry_source": "C5_OPEN30_DIRECTION", "sl": 53.0}
    st = trader.load_state("2026-11-16")
    hedge = {"source": "BEAR_ALPHA_HEDGE", "ticker": "SOXS", "atr_pct": 3.0, "why": "VIX 26",
             "signal": {"armed": True}, "notional_cap": 60.0}
    trader.entry_candidates = lambda *a, **k: [hedge]
    market = make_market(clock_at("02:00", session_date="2026-11-16"),
                         {"SOXS": {"intraday_price": 59.0}}, vix=26.0)
    actions = trader.entries(st, brackets, market, ledger())
    assert actions == []
    assert any(e["kind"] == "ENTRY_SKIP" and "C5 파일럿" in str(e.get("reason")) for e in trader.events)
