"""한국 세션 신호 모니터(core.kr_session_signal) — 계산·출력·무주문 보장 (네트워크 없음)."""
import json

import pytest

from core import kr_session_signal as ks


def test_pct_and_zscores():
    assert round(ks.pct(110, 100) * 100, 3) == 10.0
    assert ks.pct(1, 0) != ks.pct(1, 0)          # 0 나눗셈 → NaN
    assert ks.zscores([1, 2, 3, 4, 5] * 5, 3.0) == 0.0
    assert ks.zscores([1, 2, 3], 3.0) is None     # 표본 부족
    assert ks.zscores([2, 2, 2, 2, 2] * 5, 3.0) is None  # 표준편차 0


def _closes(date="2026-11-18"):
    return {
        "^KS11": [("2026-11-17", 100.0), (date, 102.0)],
        "^KQ11": [("2026-11-17", 100.0), (date, 101.0)],
        "005930.KS": [("2026-11-17", 100.0), (date, 103.0)],
        "000660.KS": [("2026-11-17", 100.0), (date, 105.0)],
        "KRW=X": [("2026-11-17", 1400.0), (date, 1390.0)],
        "SOXL": [("2026-11-16", 100.0), ("2026-11-17", 99.0)],
    }


def test_compute_returns_semis_average_and_excess():
    r = ks.compute_returns(_closes())
    assert round(r["kospi"] * 100, 2) == 2.0
    assert round(r["semis"] * 100, 2) == 4.0          # (3% + 5%) / 2
    assert round(r["semis_excess"] * 100, 2) == 2.0
    assert round(r["usdkrw"] * 100, 2) == -0.71       # 원화 강세


def test_build_signal_offline_without_network(monkeypatch):
    monkeypatch.setattr(ks, "fetch_daily", lambda t, days=40: _closes().get(t, []))
    sig = ks.build_signal(offline=False, as_of="2026-11-18")
    assert sig["as_of_date"] == "2026-11-18"
    assert sig["mode"] == "live"
    assert sig["level"] in ("RISK_OFF_ALERT", "WEAK", "NEUTRAL", "STRONG")
    # 잔차 = 4% - (0.001512 + 0.167298 * -1%)
    assert round(sig["semis_resid_pct"], 2) == 4.02
    assert "SOXL_gap" in sig["expected_open_gap"]
    assert sig["orders"].startswith("none")


def test_gap_projection_uses_calibration(monkeypatch):
    monkeypatch.setattr(ks, "fetch_daily", lambda t, days=40: _closes().get(t, []))
    sig = ks.build_signal(offline=False, as_of="2026-11-18")
    g = sig["expected_open_gap"]["SOXL_gap"]
    # 0.1772 + 0.7659 * 4.05  ≈ 3.28%
    assert abs(g["expected_pct"] - (0.1772 + 0.7659 * sig["semis_resid_pct"])) < 0.02
    assert g["r2"] < 0.5      # 갭 설명력이 낮다는 사실을 숨기지 않는다


def test_risk_off_alert_label(monkeypatch):
    closes = _closes()
    closes["005930.KS"] = [("2026-11-17", 100.0), ("2026-11-18", 90.0)]
    closes["000660.KS"] = [("2026-11-17", 100.0), ("2026-11-18", 88.0)]
    monkeypatch.setattr(ks, "fetch_daily", lambda t, days=40: closes.get(t, []))
    hist = [{"date": f"2026-08-{i:02d}", "kospi": 0.001, "semis": 0.001,
             "semis_excess": 0.0, "semis_resid": (0.001, -0.002, 0.004)[i % 3]}
            for i in range(1, 25)]
    monkeypatch.setattr(ks, "load_history", lambda: hist)
    sig = ks.build_signal(offline=False, as_of="2026-11-18")
    assert sig["level"] == "RISK_OFF_ALERT"
    assert "자동 주문 없음" in ks.format_text(sig)


def test_write_outputs_signal_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "fetch_daily", lambda t, days=40: _closes().get(t, []))
    monkeypatch.setattr(ks, "SIGNAL_PATH", str(tmp_path / "signal.json"))
    monkeypatch.setattr(ks, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(ks, "load_history", lambda: [])
    assert ks.main(["--write", "--json"]) == 0
    saved = json.loads((tmp_path / "signal.json").read_text(encoding="utf-8"))
    assert saved["as_of_date"] == "2026-11-18"
    hist = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert hist and hist[-1]["date"] == "2026-11-18"


def test_module_has_no_order_capability():
    """신호 모듈이 주문 경로를 import하지 않는다(모니터링 전용 보장)."""
    with open("core/kr_session_signal.py", "r", encoding="utf-8") as f:
        src = f.read()
    for forbidden in ("from execution", "import execution", "place_order(", ".place("):
        assert forbidden not in src
