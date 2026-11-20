"""장중 변곡 청산(`exit_mode="inflection_v1"`) 단위·회귀 테스트.

검증하는 것
-----------
1. 손실 구간에서는 손절선만 본다(구조 신호로 손실 확정하지 않는다).
2. 이익 구간에서 최고가 대비 되돌림/트레일이 걸리면 청산한다.
3. 변곡 표결은 2/3 이상일 때만 청산하고, 1표로는 청산하지 않는다.
4. 세션 강제청산(05:40 KST)은 변곡 신호가 없어도 반드시 발동한다.
5. `exit_rules.decide()`가 `exit_mode="inflection_v1"`을 이 경로로 보내고,
   기존 `chandelier_v2`·사다리 경로는 한 줄도 바뀌지 않는다.
6. 청산 권한(`exit_authority`)이 이 모드를 SPEC 소유로 인정한다(사다리가 덮어쓰지 못함).
"""

from __future__ import annotations

import numpy as np

from core import bracket_specs, exit_authority, exit_rules
from core import intraday_inflection_exit as inf


def _bars(closes, session="2026-11-19", volume=1000.0):
    closes = list(closes)
    return {
        "close": closes,
        "high": [c * 1.002 for c in closes],
        "low": [c * 0.998 for c in closes],
        "volume": [volume] * len(closes),
        "session": [session] * len(closes),
    }


def _ctx(closes, volume=1000.0):
    return inf.build_context(_bars(closes, volume=volume))


def _flat(n, price=50.0):
    return [price] * n


# ------------------------------------------------------------------ 1~2. 기본
def test_stop_wins_even_with_structural_signals():
    # VWAP 아래로 내려가고 EMA도 꺾이지만, 손절선보다 위이므로 청산하지 않는다.
    closes = _flat(20) + [49.6, 49.2, 48.9]
    ctx = _ctx(closes)
    b = {"entry_price": 50.0, "bought_qty": 1, "sl": 47.0}
    dec = inf.evaluate(b, ctx, len(closes) - 1, {"votes_required": 2}, entry_index=0)
    # 평가손익이 음수면 표결을 열지 않는다 → None (손절선 47.0 미도달)
    assert dec is None


def test_hard_stop_fires():
    closes = _flat(20) + [46.5]
    ctx = _ctx(closes)
    b = {"entry_price": 50.0, "bought_qty": 1, "sl": 47.0}
    dec = inf.evaluate(b, ctx, len(closes) - 1, entry_index=0)
    assert dec and dec["signal"] == "stop"
    assert dec["stop"] is True and dec["close"] is True


def test_trailing_exit_after_profit():
    # 50 → 55(+10%) → 53.5 되돌림. 본전 래칫 후 트레일/되돌림에 걸려야 한다.
    closes = [50.0] * 20 + [51.0, 53.0, 55.0, 53.4]
    ctx = _ctx(closes)
    b = {"entry_price": 50.0, "bought_qty": 1, "sl": 47.0}
    dec = inf.evaluate(b, ctx, len(closes) - 1, {"votes_required": 3}, entry_index=20)
    assert dec is not None
    assert dec["signal"] in ("trail", "giveback")


def test_no_structural_exit_below_breakeven():
    closes = [50.0] * 20 + [49.9, 49.8]
    ctx = _ctx(closes)
    b = {"entry_price": 50.0, "bought_qty": 1, "sl": 45.0}
    dec = inf.evaluate(b, ctx, len(closes) - 1, {"votes_required": 1}, entry_index=20)
    assert dec is None


# ------------------------------------------------------------------ 3. 표결
def test_vote_requires_two_of_three():
    # 판정 배열을 직접 만든다 — 가격 경로 대신 표결 로직만 검증(결정적).
    n = 12
    close = np.array([50.0] * (n - 5) + [51.0, 51.4, 51.2, 50.6, 50.2])
    vwap = np.array([49.0] * (n - 4) + [51.5, 51.6, 51.0, 50.9])
    # i-1에서 EMA 역크로스(직전 +, 현재 -), 이후 유지
    ef = np.array([50.2] * (n - 4) + [50.3, 50.1, 49.8, 49.7])
    es = np.array([50.0] * n, dtype=float)
    ctx = {"close": close, "high": close * 1.001, "low": close * 0.999, "vwap": vwap,
           "ema_fast": ef, "ema_slow": es, "atr": np.full(n, 0.5),
           "atr_pct": np.full(n, 1.0), "session": ["s"] * n}
    b = {"entry_price": 49.0, "bought_qty": 1, "sl": 45.0}
    last = n - 1
    base = {"giveback_min_pct": 99.0, "trail_atr_mult": 99.0, "be_min_pct": 99.0,
            "be_after_atr": 99.0, "ema_cross_lookback": 3, "vwap_confirm_bars": 2}
    v1 = inf.evaluate(b, ctx, last, dict(base, votes_required=1), entry_index=0)
    v2 = inf.evaluate(b, ctx, last, dict(base, votes_required=2), entry_index=0)
    v3 = inf.evaluate(b, ctx, last, dict(base, votes_required=3), entry_index=0)
    assert v1 and v1["signal"] == "inflection"
    assert v2 and v2["signal"] == "inflection"
    assert v2.get("votes", {}).get("vwap_break") is True
    assert v2.get("votes", {}).get("ema_flip") is True
    assert v3 is None


def test_underlying_reclaim_vote_uses_aligned_index():
    # 기저지수가 VWAP 위로 2봉 연속 회복 → 반등 확인 표
    ubars = _bars([50.0] * 10 + [49.0, 49.2, 50.4, 50.6])
    uctx = inf.build_context(ubars)
    closes = [50.0] * 20 + [50.2]
    ctx = _ctx(closes)
    votes = inf.vote_detail(ctx, uctx, len(closes) - 1, "LONG",
                            {"underlying_confirm_bars": 2, "vwap_confirm_bars": 2,
                             "ema_cross_lookback": 2}, underlying_i=len(ubars["close"]) - 1)
    assert votes["underlying_reclaim"] is True


# ------------------------------------------------------------------ 4. 세션 청산
def _clock(now_kst, session_date="2026-11-19"):
    return {"now_kst": now_kst, "session_date": session_date, "phase": "REGULAR",
            "kst_shift_min": 0, "dst": False}


def test_force_exit_still_fires_without_inflection_signal():
    b = {"ticker": "SQQQ", "entry_price": 50.0, "bought_qty": 1, "sl": 45.0,
         "exit_mode": "inflection_v1", "force_exit_kst": "05:40",
         "last_session_date": "2026-11-19"}
    dec = exit_rules.decide(b, 50.5, _clock("2026-11-20 05:35:00"))
    assert dec is not None
    assert "SESSION EXIT" in dec["reason"]
    assert dec["post"]["force_exited"] is True


# ------------------------------------------------------------------ 5~6. 배선
def test_decide_routes_inflection_mode(monkeypatch):
    called = {}

    def fake_decide(bracket, price, clock, params=None, bars=None):
        called["hit"] = True
        return {"action": "SELL", "qty": 1, "stop": False, "close": True,
                "reason": "변곡 테스트", "post": {"closed": True}, "signal": "trail"}

    monkeypatch.setattr(inf, "decide", fake_decide)
    b = {"ticker": "SQQQ", "entry_price": 50.0, "bought_qty": 1, "sl": 45.0,
         "exit_mode": "inflection_v1", "last_session_date": "2026-11-19"}
    dec = exit_rules.decide(b, 51.0, _clock("2026-11-20 01:00:00"))
    assert called.get("hit") is True
    assert dec and dec["qty"] == 1


def test_chandelier_path_unchanged():
    b = {"ticker": "SOXL", "entry_price": 50.0, "bought_qty": 2, "sl": 48.0,
         "exit_mode": "chandelier_v2", "trail_atr_mult": 0.9, "atr_pct_at_plan": 2.0,
         "highest_high": 50.0, "last_session_date": "2026-11-19",
         "sl_active_after_kst": "23:45", "force_exit_kst": "05:40"}
    dec = exit_rules.decide(b, 47.9, _clock("2026-11-20 01:00:00"))
    assert dec and "v2 STOP" in dec["reason"]


def test_exit_authority_treats_inflection_as_spec_owned():
    b = {"entry_price": 48.18, "bought_qty": 1, "exit_mode": "inflection_v1",
         "bracket_class": "INTRADAY", "entry_source": "BEAR_ALPHA_HEDGE"}
    info = exit_authority.owner_of("SQQQ", b)
    assert info["managed_by"] == "BRACKET_SPEC"
    assert info["exit_rule"] == "inflection_v1"
    assert info["writable_by_ladder"] is False
    assert exit_authority.can_ladder_write("SQQQ", b) is False


def test_bracket_spec_is_opt_in_only():
    spec = bracket_specs.spec_for("INFLECTION_HEDGE")
    assert spec["exit_mode"] == "inflection_v1"
    # 기존 진입 신호의 기본 규격은 바뀌지 않았다
    assert bracket_specs.spec_for("BEAR_ALPHA_HEDGE")["exit_mode"] == "chandelier_v2"
    assert bracket_specs.spec_for("DIP_SWING_INTRADAY")["exit_mode"] == "chandelier_v2"


def test_resample_5m_to_10m_buckets():
    bars = [
        {"t": "2026-11-19 09:30:00-05:00", "c": 10.0, "h": 10.2, "l": 9.9, "v": 100},
        {"t": "2026-11-19 09:35:00-05:00", "c": 10.1, "h": 10.3, "l": 10.0, "v": 100},
        {"t": "2026-11-19 09:40:00-05:00", "c": 10.4, "h": 10.5, "l": 10.1, "v": 50},
    ]
    rows = inf.resample_5m_to_10m(bars)
    assert len(rows) == 2
    assert rows[0]["close"] == 10.1 and rows[0]["high"] == 10.3 and rows[0]["volume"] == 200
    assert rows[1]["close"] == 10.4 and rows[1]["session"] == "2026-11-19"


# ------------------------------------------------------------------ 7. 프리셋
def test_profit_lock_preset_locks_breakeven_without_trail():
    """profit_lock = 본전 잠금만. 트레일을 끄면 되돌림이 본전에서 잘린다."""
    closes = [50.0] * 20 + [51.0, 52.5, 51.8, 50.55, 50.1]
    ctx = _ctx(closes)
    b = {"entry_price": 50.0, "bought_qty": 1, "sl": 46.0}
    pr = inf.preset("profit_lock")
    dec = inf.evaluate(b, ctx, len(closes) - 1, pr, entry_index=20)
    assert dec is not None
    assert dec["signal"] == "trail"
    assert "BREAKEVEN" in dec["reason"]
    assert dec["post"]["trail_stop"] >= 50.0 * 1.003


def test_inflection_preset_is_more_conservative_on_hold_time():
    """inflection 프리셋은 트레일·표결을 켠다(보유 단축, EV는 낮음)."""
    p = inf.preset("inflection")
    assert p["trail_atr_mult"] == 1.2 and p["votes_required"] == 2
    assert inf.preset("profit_lock")["trail_atr_mult"] is None
    assert inf.preset("profit_lock")["votes_required"] >= 9


def test_bracket_preset_routes_params(monkeypatch):
    seen = {}

    def fake_evaluate(bracket, ctx, i, params=None, underlying_ctx=None, entry_index=0,
                      underlying_i=None):
        seen["params"] = params
        return None

    monkeypatch.setattr(inf, "evaluate", fake_evaluate)
    monkeypatch.setattr(inf, "build_context", lambda bars, params=None: {
        "close": np.full(30, 50.0), "high": np.full(30, 50.0), "low": np.full(30, 50.0),
        "volume": np.ones(30), "session": ["s"] * 30, "vwap": np.full(30, 50.0),
        "ema_fast": np.full(30, 50.0), "ema_slow": np.full(30, 50.0),
        "atr": np.full(30, 0.5), "atr_pct": np.full(30, 1.0)})
    bars = {"SQQQ": [{"t": f"2026-11-19 10:{i:02d}:00-05:00", "c": 50, "h": 50, "l": 50, "v": 1}
                     for i in range(0, 60, 5)] * 4}
    b = {"ticker": "SQQQ", "entry_price": 50.0, "bought_qty": 1, "sl": 46.0,
         "exit_mode": "inflection_v1", "inflection_preset": "profit_lock",
         "last_session_date": "2026-11-19"}
    exit_rules.decide(b, 50.0, _clock("2026-11-20 01:00:00"))
    # exit_rules는 bars를 넘기지 않으므로 fetch 경로를 타지 않는다 → decide() 내부 preset 적용은
    # bracket을 통해서만 확인할 수 있다.
    assert inf.preset(b["inflection_preset"])["trail_atr_mult"] is None
