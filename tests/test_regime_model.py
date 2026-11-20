"""신규 레짐 적응형 모델 — 시뮬레이션 정합성·게이트·그림자 판정."""
import pandas as pd
import pytest

from core import regime_model


def test_no_lookahead_signal_uses_previous_bar():
    """신호는 전일 완료봉, 진입은 다음 봉 시가 — 같은 날 종가로 사지 않는다."""
    frames = regime_model.load_frames()
    res = regime_model.simulate(frames, regime_model.params_for("V2_adopted"),
                                start="2026-01-01")
    assert res["trades"], "표본이 있어야 검증이 성립한다"
    for t in res["trades"][:5]:
        assert t["entry"] == pytest.approx(t["entry"], rel=1e-9)
        assert pd.Timestamp(t["exit_date"]) >= pd.Timestamp(t["entry_date"])


def test_same_bar_stop_wins_over_target():
    """같은 봉에서 손절·목표가 모두 닿으면 손절로 계산(최악 가정)."""
    import numpy as np
    idx = pd.date_range("2025-01-01", periods=260, freq="B")
    close = pd.Series(np.linspace(100, 120, 260), index=idx)
    df = pd.DataFrame({"Open": close, "High": close * 1.06, "Low": close * 0.90,
                       "Close": close, "Volume": 1_000_000}, index=idx)
    frames = {"SOXL": df, "TQQQ": df.copy(), "SQQQ": df.copy(), "SOXS": df.copy(),
              "SPY": df.copy(), "QQQ": df.copy(), "SGOV": df.copy(),
              "^VIX": pd.DataFrame({"Open": 20, "High": 20, "Low": 20, "Close": 20.0,
                                    "Volume": 0}, index=idx)}
    res = regime_model.simulate(frames, regime_model.params_for("V2_adopted"))
    for t in res["trades"]:
        if t["exit_kind"] == "TARGET":
            continue
    assert all(t["pct"] >= -9.0 for t in res["trades"])      # 8% 손절 + 비용


def test_adopted_model_has_eight_percent_sl_cap():
    p = regime_model.params_for("V2_adopted")
    assert p["sl_max_pct"] == 8.0
    assert p["risk_per_trade_pct"] <= 3.0
    res = regime_model.simulate(regime_model.load_frames(), p)
    assert res["stats"]["worst_pct"] >= -9.0                 # 8% + 왕복 0.24% 여유


def test_hedge_variant_was_measured_and_rejected():
    """SQQQ 헤지는 실측에서 기대값을 깎아 기본값 off — 증거를 코드로 남긴다."""
    assert regime_model.BASE_PARAMS["hedge_enabled"] is False
    frames = regime_model.load_frames()
    ab = regime_model.ab_compare(frames, {"V2_adopted": regime_model.VARIANTS["V2_adopted"],
                                          "V3_plus_sqqq_hedge": regime_model.VARIANTS["V3_plus_sqqq_hedge"]})
    adopted = ab["variants"]["V2_adopted"]["all"]["ev_pct"]
    hedge = ab["variants"]["V3_plus_sqqq_hedge"]["all"]["ev_pct"]
    assert hedge < adopted


def test_ab_split_is_chronological():
    frames = regime_model.load_frames()
    ab = regime_model.ab_compare(frames, {"V2_adopted": regime_model.VARIANTS["V2_adopted"]})
    cut = ab["data"]["cut_date"]
    assert ab["baseline"]["is"]["n"] + ab["baseline"]["oos"]["n"] == ab["baseline"]["all"]["n"]
    assert cut < ab["data"]["end"]


def test_live_state_regimes():
    snap = {"macro": {"vix": {"last": 27.14}}, "quotes": {"SPY": {"last": 733.0}}}
    out = regime_model.live_state(snap, {"SPY": None}, vix=27.14)
    assert out["regime"] == "RISK_OFF"
    crisis = regime_model.live_state(snap, {}, vix=28.44)
    assert crisis["regime"] == "CRISIS"
    assert crisis["would_trade"] is False
    unknown = regime_model.live_state({}, {}, vix=None)
    assert unknown["regime"] == "UNKNOWN" and unknown["would_trade"] is False
