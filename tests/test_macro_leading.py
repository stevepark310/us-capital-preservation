"""CUBE-M8(8대 거시·자산 선행지표) — 발표시차·룩어헤드·복합·정책 게이트 검증."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core import macro_leading as m8


# --------------------------------------------------------------------- fixtures
def _fake_raw(n: int = 1500, start: str = "2016-01-01") -> dict:
    """네트워크 없이 모든 성분을 만들 수 있는 결정론적 합성 원계열."""
    rng = np.random.default_rng(7)
    idx = pd.bdate_range(start, periods=n)
    prices = {}
    for i, sym in enumerate(m8.PRICE_SYMBOLS):
        drift = 0.0002 * (1 if i % 3 else -1)
        vals = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
        prices[sym] = pd.DataFrame({"Close": vals}, index=idx)
    fred = {}
    for sid, meta in m8.FRED_META.items():
        if meta["freq"] == "monthly":
            mi = pd.date_range("2008-01-01", periods=260, freq="MS")
            fred[sid] = pd.Series(100 + np.arange(260) * 0.3 + rng.normal(0, 0.8, 260), index=mi)
        elif meta["freq"] == "weekly":
            wi = pd.date_range("2008-01-05", periods=1000, freq="W-SAT")
            fred[sid] = pd.Series(300000 + rng.normal(0, 8000, 1000), index=wi)
        else:
            fred[sid] = pd.Series(3.0 + np.cumsum(rng.normal(0, 0.02, n)), index=idx)
    return {"fred": fred, "prices": prices}


@pytest.fixture(scope="module")
def raw():
    return _fake_raw()


# ------------------------------------------------------------- publication lag
def test_monthly_value_known_only_after_release_lag():
    """월간 값은 '기말 + lag' 전에는 알 수 없다(룩어헤드 차단)."""
    idx = pd.date_range("2024-01-01", "2024-06-30", freq="B")
    monthly = pd.Series([100.0, 200.0], index=pd.to_datetime(["2024-01-01", "2024-02-01"]))
    aligned = m8.align_availability(monthly, "monthly", 13, idx)

    # 1월 값(기말 1/31 + 13일 = 2/13) 이전에는 아직 모른다
    assert pd.isna(aligned.loc["2024-02-12"])
    assert aligned.loc["2024-02-13"] == 100.0
    # 2월 값(2/29 + 13 = 3/13) 이전에는 1월 값이 유지된다
    assert aligned.loc["2024-03-12"] == 100.0
    assert aligned.loc["2024-03-13"] == 200.0


def test_extra_lag_shifts_availability_later():
    """강건성 검정(+10일)은 값의 공개 시점을 뒤로만 미룬다(앞당기지 않는다)."""
    idx = pd.date_range("2024-01-01", "2024-06-30", freq="B")
    monthly = pd.Series([100.0], index=pd.to_datetime(["2024-01-01"]))
    base = m8.align_availability(monthly, "monthly", 13, idx)
    lagged = m8.align_availability(monthly, "monthly", 13, idx, extra_lag_days=10)
    assert pd.isna(lagged.loc["2024-02-22"])
    assert lagged.loc["2024-02-23"] == 100.0
    first_base = base.first_valid_index()
    first_lag = lagged.first_valid_index()
    assert first_lag >= first_base


# ------------------------------------------------------------------ look-ahead
def test_truncating_future_data_does_not_change_past_signal(raw):
    """t 이후 데이터를 모두 지워도 t 시점 z는 같아야 한다(미래 정보 미사용)."""
    cal = raw["prices"]["SPY"].index
    full = m8.build_panel(raw, cal, sign_fit={})
    k = len(cal) - 260
    cut = cal[k]

    trunc_raw = {
        "fred": {sid: s.loc[:cut] for sid, s in raw["fred"].items()},
        "prices": {sym: f.loc[:cut] for sym, f in raw["prices"].items()},
    }
    trunc = m8.build_panel(trunc_raw, cal[:k + 1], sign_fit={})

    a = full["composite"].reindex(cal[:k + 1])
    b = trunc["composite"]
    both = pd.concat([a.rename("full"), b.rename("cut")], axis=1).dropna()
    assert len(both) > 100
    assert np.allclose(both["full"], both["cut"], atol=1e-9)


def test_rolling_z_uses_past_window_only():
    s = pd.Series(np.arange(300, dtype=float), index=pd.bdate_range("2020-01-01", periods=300))
    z = m8._rolling_z(s, 100)
    # 단조 증가 계열 → 과거 평균보다 항상 크므로 z>0이고, 미래 값을 쓰면 0이 될 수 없다
    assert z.iloc[-1] > 0
    assert z.iloc[:24].isna().all()          # 창이 차기 전에는 판정하지 않는다


# -------------------------------------------------------------------- composite
def test_composite_is_weighted_block_average_and_clipped(raw):
    cal = raw["prices"]["SPY"].index
    panel = m8.build_panel(raw, cal, sign_fit={})
    blocks = panel["block_z"].dropna()
    assert len(blocks) > 0
    weights = pd.Series({k: m8.BLOCK_META[k]["weight"] for k in blocks.columns})
    expect = (blocks.mul(weights, axis=1).sum(axis=1) / weights.sum()).clip(-3, 3)
    got = panel["composite"].reindex(blocks.index)
    assert np.allclose(got.values, expect.values, atol=1e-9)
    assert got.abs().max() <= 3.0 + 1e-9


def test_sign_fit_flips_are_applied(raw):
    cal = raw["prices"]["SPY"].index
    base = m8.build_panel(raw, cal, sign_fit={})
    flipped = m8.build_panel(raw, cal, sign_fit={"curve_10y2y": -1.0})
    a = base["member_z"]["curve_10y2y"].dropna()
    b = flipped["member_z"]["curve_10y2y"].dropna()
    common = a.index.intersection(b.index)
    assert np.allclose(a.loc[common].values, -b.loc[common].values, atol=1e-12)


# ----------------------------------------------------------------------- policy
def test_regime_thresholds_are_monotone():
    prev = 99.0
    for z in (2.0, 0.8, 0.3, 0.0, -0.5, -1.0, -2.0):
        scale = m8.regime_for(z)[2]
        assert scale <= prev
        prev = scale
    assert m8.regime_for(2.0)[0] == "EXPANSION_STRONG"
    assert m8.regime_for(-2.0)[0] == "CONTRACTION"


def test_linear_scale_bounds_and_monotonicity():
    vals = [m8.linear_scale(z) for z in (-5, -1.6, -1.0, 0.0, 0.6, 1.0, 5.0)]
    assert vals == sorted(vals)
    p = m8.EXPOSURE_LINEAR
    assert all(p["floor"] <= v <= p["cap"] for v in vals)
    assert m8.linear_scale(None) == p["floor"]


def test_policy_file_is_shadow_by_default():
    """실전 사이징은 동훈님 승인 전까지 꺼져 있어야 한다(기본값 회귀 방지)."""
    with open(m8.POLICY_PATH, encoding="utf-8") as f:
        pol = json.load(f)
    assert pol["enabled_for_sizing"] is False
    assert pol["mode"] == "shadow"


def test_risk_scale_shadow_returns_one(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"mode": "shadow", "enabled_for_sizing": False}), encoding="utf-8")
    res = {"composite": {"z": -1.2, "regime": "SLOWDOWN", "regime_ko": "둔화"}, "as_of": "2026-11-20"}
    scale, meta = m8.risk_scale(res, policy_path=str(policy))
    assert scale == 1.0
    assert meta["applied_scale"] == 1.0
    assert meta["shadow_scale"] < 1.0            # 계수는 계산되지만 적용은 안 된다


def test_risk_scale_active_only_when_enabled(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"mode": "active", "enabled_for_sizing": True}), encoding="utf-8")
    res = {"composite": {"z": -1.0}, "as_of": "2026-11-20"}
    scale, meta = m8.risk_scale(res, policy_path=str(policy))
    assert scale == pytest.approx(0.5, abs=1e-6)
    assert meta["applied_scale"] == pytest.approx(0.5, abs=1e-6)


def test_attach_to_lei_uses_published_file(tmp_path, monkeypatch):
    doc = {"composite": {"z": -0.4, "regime": "SLOWDOWN", "regime_ko": "둔화", "index": 96.0},
           "engine": "CUBE-M8 v1", "as_of": "2026-11-20", "blocks": [], "policy": {"mode": "shadow"}}
    p = tmp_path / "macro_leading.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(m8, "OUT_PATH", str(p))
    out = {}
    m8.attach_to_lei(out)
    assert out["macro8"]["z"] == -0.4
    assert out["macro8"]["regime_ko"] == "둔화"


# --------------------------------------------------------------------- backtest
def test_backtest_exposure_uses_previous_close_signal():
    """백테스트 회귀 테스트 — t일 종가에 정한 노출은 t+1일 수익부터 적용돼야 한다."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m8_bt", "scripts/run_macro_lei_backtest.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)

    idx = pd.bdate_range("2024-01-01", periods=5)
    exposure = pd.Series([0.0, 0.0, 1.0, 1.0, 1.0], index=idx)
    ret = pd.Series([0.01] * 5, index=idx)
    cash = pd.Series([100.0] * 5, index=idx)          # 파킹 수익률 0
    run = bt._run(exposure, ret, cash)

    assert run["daily"].iloc[0] == pytest.approx(0.0)          # 첫날은 노출 없음
    assert run["daily"].iloc[1] == pytest.approx(0.0)          # 신호 전날 → 수익 없음
    # 신호가 선 날(2): 전일 비중(0)으로 수익 → 그날 급등을 먹지 않는다. 회전비용만 나간다.
    assert run["daily"].iloc[2] == pytest.approx(-0.0012)
    assert run["exposure"].iloc[2] == pytest.approx(0.0)
    # 그 다음날부터 노출이 적용된다.
    assert run["daily"].iloc[3] == pytest.approx(0.01)
    assert run["exposure"].iloc[3] == pytest.approx(1.0)


def test_backtest_reads_published_signs_shape():
    signs = m8.load_sign_fit()
    if not signs:                                     # 아직 백테스트 전이면 통과
        pytest.skip("부호 적합 파일 없음 — 사전부호로 동작")
    assert set(signs.values()) <= {-1.0, 1.0}
    assert all(k in {s["key"] for s in m8.MEMBER_SPECS} for k in signs)
