"""동적 유니버스 스크리너 — 코어 4종목 필수 + 실측 지표로만 추가 종목을 고른다."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from core import universe_screener as us


def make_frame(n=90, start=100.0, drift=-0.004, vol=2_000_000.0):
    idx = pd.bdate_range("2025-06-02", periods=n)
    closes = start * np.cumprod(np.full(n, 1.0 + drift))
    return pd.DataFrame({"Open": closes, "High": closes * 1.005, "Low": closes * 0.995,
                         "Close": closes, "Volume": np.full(n, vol)}, index=idx)


CORE = us.CORE_UNIVERSE
POOL = CORE + ["AAA", "BBB", "CCC", "ENVX"]


def frames():
    return {
        "SOXL": make_frame(), "TQQQ": make_frame(), "SQQQ": make_frame(drift=0.0),
        "SOXS": make_frame(drift=0.005),               # 인버스 — 상승 추세
        "AAA": make_frame(),                            # 유동적 · 과매도 → 롱 후보
        "BBB": make_frame(vol=10_000.0),                # 거래대금 부족
        "CCC": make_frame(start=500.0),                 # 1주 $220 초과 → affordable=false
        "ENVX": make_frame(),                           # 금지 종목
    }


def test_metrics_and_scores():
    m = us.metrics_of(make_frame())
    assert m and m["close"] > 0 and m["dollar_volume_20d"] > 0
    assert m["rsi2"] < 20 and m["pct_b"] < 0.15          # 하락 추세 → 과매도
    assert us.score_long(m) > 0
    flat = us.metrics_of(make_frame(drift=0.0))
    assert us.score_long(flat) < us.score_long(m)        # 눌림이 아니면 점수가 낮다
    rising = us.metrics_of(make_frame(drift=0.005))
    assert us.score_hedge(rising) > us.score_hedge(flat)


def test_screen_selects_liquid_affordable_oversold_only():
    doc = us.screen(frames=frames(), pool=POOL, save_doc=False)
    assert doc["source"] == "injected"
    assert "AAA" in doc["selected_long"]
    for bad in ("BBB", "CCC", "ENVX"):
        assert bad not in doc["selected_long"]
        assert any(s["ticker"] == bad for s in doc["skipped"]), bad
    reasons = {s["ticker"]: s["reason"] for s in doc["skipped"]}
    assert "금지" in reasons["ENVX"]
    assert "거래대금" in reasons["BBB"]
    # 코어 4종목은 선정과 무관하게 허용 목록에 남는다
    assert set(CORE).issubset(set(doc["allowed"]))
    assert doc["allowed"][:len(CORE)] == CORE


def test_core_tickers_are_always_kept_even_outside_screen_conditions():
    fr = frames()
    fr["SOXL"] = make_frame(drift=-0.05)            # ATR이 커도(범위 밖) 코어는 유지
    doc = us.screen(frames=fr, pool=POOL, save_doc=False)
    core = {c["ticker"] for c in doc["core_detail"]}
    assert set(CORE).issubset(core)
    assert not any(s["ticker"] == "SOXL" for s in doc["skipped"])
    assert set(CORE).issubset(set(doc["allowed"]))


def test_screen_fails_closed_without_data():
    doc = us.screen(frames={}, pool=POOL, save_doc=False)
    assert doc["selected_long"] == [] and doc["selected_hedge"] == []
    assert doc["allowed"] == CORE


def test_selected_symbols_respects_staleness(tmp_path, monkeypatch):
    path = tmp_path / "dynamic_universe.json"
    monkeypatch.setattr(us, "OUT_PATH", str(path))
    now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)
    path.write_text(json.dumps({
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "allowed": CORE + ["AAA"], "selected_long": ["AAA"], "selected_hedge": []}), encoding="utf-8")
    assert us.selected_symbols(max_age_h=1.0) == ["AAA"]
    assert us.effective_universe(max_age_h=1.0) == CORE + ["AAA"]

    path.write_text(json.dumps({
        "generated_at_kst": (now - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"),
        "allowed": CORE + ["AAA"], "selected_long": ["AAA"], "selected_hedge": []}), encoding="utf-8")
    assert us.selected_symbols(max_age_h=6.0) == []       # 오래된 선정은 쓰지 않는다
    assert us.effective_universe(max_age_h=6.0) == CORE


def test_sync_policy_universe_writes_core_plus_selected(tmp_path, monkeypatch):
    path = tmp_path / "swing_approval.json"
    path.write_text(json.dumps({"autonomous_execution": {"universe": ["SOXL", "TQQQ"]}}), encoding="utf-8")
    monkeypatch.setattr(us, "POLICY_PATH", str(path))
    doc = {"allowed": CORE + ["AAA"], "selected_long": ["AAA"], "selected_hedge": [],
           "generated_at_kst": "2026-11-26 21:00:00", "as_of": "2026-11-25",
           "rule": "테스트 규칙"}
    out = us.sync_policy_universe(doc)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    uni = cfg["autonomous_execution"]["universe"]
    assert uni[:len(CORE)] == CORE and "AAA" in uni
    assert cfg["autonomous_execution"]["dynamic_universe"]["selected_long"] == ["AAA"]
    assert out["changed"] is True and "AAA" in out["added"]


def test_banned_tickers_never_selected():
    fr = frames()
    doc = us.screen(frames=fr, pool=["ENVX"], save_doc=False)
    assert doc["allowed"] == CORE
    assert any(s["ticker"] == "ENVX" and "금지" in s["reason"] for s in doc["skipped"])
