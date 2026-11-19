"""검증 실험실(desk_lab) — 일봉 경로 근사가 결정적으로 같은 결과를 내는가."""
import numpy as np
import pandas as pd
import pytest

from core import desk_lab


def frame(closes, volumes=None, highs=None, lows=None, opens=None):
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    c = np.asarray(closes, dtype=float)
    o = np.asarray(opens if opens is not None else c * 0.999, dtype=float)
    h = np.asarray(highs if highs is not None else c * 1.01, dtype=float)
    l = np.asarray(lows if lows is not None else c * 0.99, dtype=float)
    v = np.asarray(volumes if volumes is not None else np.full(len(c), 1_000_000.0), dtype=float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


def dip_then_rally(n=120):
    """20일 눌림(RSI2 낮음·%B 낮음) 뒤 반등하는 계열."""
    closes = list(np.linspace(100, 100, 40))
    closes += list(np.linspace(100, 78, 25))       # 눌림
    closes += list(np.linspace(78, 120, 55))       # 반등
    vols = [1_000_000] * len(closes)
    for i in range(60, 70):
        vols[i] = 3_000_000                        # RVOL 급증
    return frame(closes, vols)


def test_simulate_is_deterministic_and_produces_trades():
    daily = {"TQQQ": dip_then_rally()}
    a = desk_lab.simulate(daily, {"entry_filters": {"rsi2_max": 30, "pct_b_max": 0.5,
                                                    "rvol_required": False}})
    b = desk_lab.simulate(daily, {"entry_filters": {"rsi2_max": 30, "pct_b_max": 0.5,
                                                    "rvol_required": False}})
    assert a["trades"] == b["trades"]
    assert a["stats"]["n"] >= 1
    for t in a["trades"]:
        assert t["kind"] in ("STOP/TRAIL", "TARGET", "TIME")
        # 진입·청산가는 소수 3자리로 반올림해 기록하므로 허용오차 0.01%p
        assert abs(t["pct_net"] - ((t["exit"] / t["entry"] - 1) * 100 - desk_lab.ROUND_TRIP_FEE_PCT)) < 0.01


def test_entry_filter_is_respected():
    daily = {"TQQQ": dip_then_rally()}
    loose = desk_lab.simulate(daily, {"entry_filters": {"rsi2_max": 100, "pct_b_max": 1.0,
                                                        "rvol_required": False}})
    tight = desk_lab.simulate(daily, {"entry_filters": {"rsi2_max": 1, "pct_b_max": 0.0,
                                                        "rvol_min": 9.9, "rvol_required": True}})
    assert loose["stats"]["n"] >= 1
    assert tight["stats"]["n"] == 0


def test_is_oos_split_is_chronological():
    trades = [{"entry_date": f"2026-01-{d:02d}", "pct_net": 1.0} for d in range(1, 11)]
    out = desk_lab.split_is_oos(trades, is_frac=0.7)
    assert out["cut_date"] == "2026-01-07"
    assert len(out["is"]) == 7 and len(out["oos"]) == 3
    assert max(t["entry_date"] for t in out["is"]) <= min(t["entry_date"] for t in out["oos"])


def test_stats_math():
    trades = [{"pct_net": 2.0, "kind": "TARGET"}, {"pct_net": -1.0, "kind": "STOP/TRAIL"}]
    s = desk_lab.stats_of(trades)
    assert s["ev_pct"] == 0.5 and s["profit_factor"] == 2.0
    assert s["win_rate_pct"] == 50.0 and s["mdd_pct"] == -1.0
    assert s["stop_share_pct"] == 50.0


def test_evaluate_variant_structure():
    daily = {"TQQQ": dip_then_rally(), "SOXL": dip_then_rally()}
    ev = desk_lab.evaluate_variant(daily, {"entry_filters": {"pct_b_max": 0.05}},
                                   {"entry_filters": {"rsi2_max": 30, "pct_b_max": 0.5}})
    for key in ("baseline", "candidate", "delta_oos_ev_pct", "data_fingerprint", "cut_date"):
        assert key in ev
    assert "oos" in ev["candidate"] and "is" in ev["baseline"]
    assert ev["data_as_of"]


def test_fingerprint_changes_with_data():
    a = {"TQQQ": dip_then_rally()}
    b = {"TQQQ": dip_then_rally().iloc[:-1]}
    assert desk_lab.data_fingerprint(a) != desk_lab.data_fingerprint(b)
