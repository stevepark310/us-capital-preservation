"""1주 단가 상한 확대 — 가용 현금 기준 $700대 대형주 매수 경로 (2026-11-27 13:37 지시).

계좌는 정수주 단위($881)라 $220 예산 상한 아래에서는 AAPL·MSFT·META·SPY 같은
1주가 $220을 넘는 종목이 영구히 0주였다. 이 파일은 ① 단가 상한 계산 ② 예산 부족을
이유로 한 거부가 사라지는지 ③ 다른 가드레일(손실 리스크·현금 마진)은 그대로인지 검증한다.
"""
import copy
import json

import pytest

from core import autotrader, execution_policy, portfolio_quant_risk as qr
from core.autotrader import AutonomousTrader

from conftest import FakeRouter, clock_at

BIG = "TESTBIG"          # 1주 $717 — $220 예산의 3배가 넘는 고가 종목


# ------------------------------------------------------------------ policy layer
def test_unit_price_cap_is_bounded_by_cash_and_policy():
    pol = execution_policy.load_policy()
    info = execution_policy.unit_price_cap(pol, 780.56)
    assert info["hard_cap_usd"] == 750.0
    assert info["cap_usd"] == pytest.approx(740.56, abs=0.01)      # 현금 780.56 − 마진 40
    assert info["binding"] == "cash"
    # 현금이 충분하면 정책 상한이 상한이다.
    assert execution_policy.unit_price_cap(pol, 5000.0)["cap_usd"] == 750.0
    # 현금이 줄면 상한도 같이 줄어든다(동적 연동).
    assert execution_policy.unit_price_cap(pol, 300.0)["cap_usd"] == pytest.approx(260.0)


def test_single_share_budget_allows_700s_and_refuses_above_cap():
    pol = execution_policy.load_policy()
    ok = execution_policy.single_share_budget(717.0, 780.56, pol)
    assert ok["ok"] is True
    assert ok["budget_usd"] == 717.0
    assert ok["cash_after_usd"] == pytest.approx(63.56)
    too_big = execution_policy.single_share_budget(1087.0, 780.56, pol)   # COST 1주
    assert too_big["ok"] is False and "단가 상한" in too_big["reason"]
    no_cash = execution_policy.single_share_budget(717.0, 700.0, pol)
    assert no_cash["ok"] is False


def test_policy_file_carries_the_widened_cap():
    pol = execution_policy.load_policy()
    g = execution_policy.guardrails(pol)
    assert g["max_unit_price_usd"] == 750.0
    assert g["single_share_cash_reserve_usd"] == 40.0
    # 하드락(1회 예산 $220·현금 마진 $150)은 건드리지 않았다.
    assert g["entry_budget_cap_usd"] == 220.0
    assert g["cash_reserve_floor_usd"] == 150.0


# ------------------------------------------------------------------ quant layer
def test_quant_allows_one_large_cap_share_only_via_single_share_path():
    empty = {"positions": {}, "unpriced": []}
    normal = qr.assess_entry(BIG, 1, 717.0, 881.24, 3.0, empty)
    assert normal["ok"] is False and normal["approved_qty"] == 0   # 기존 1% 리스크·75% 총노출
    wide = qr.assess_entry(BIG, 1, 717.0, 881.24, 3.0, empty, large_cap_single_share=True)
    assert wide["ok"] is True and wide["approved_qty"] == 1
    assert wide["single_share_limits"] is True


def test_quant_single_share_still_caps_stop_risk():
    empty = {"positions": {}, "unpriced": []}
    # $717 × 10% 손절 = $71.7 = 자산의 8.1% → 3% 한도 초과로 거부
    risk = qr.assess_entry(BIG, 1, 717.0, 881.24, 10.0, empty, large_cap_single_share=True)
    assert risk["ok"] is False and risk["approved_qty"] == 0


def test_quant_evaluates_dynamic_universe_symbols_generically():
    """동적 유니버스 종목(비-FACTORS)도 일반주(factor 1·충격 8%)로 계산한다."""
    empty = {"positions": {}, "unpriced": []}
    out = qr.assess_entry("SLV", 2, 37.10, 881.24, 4.0, empty)
    assert out["ok"] is True and out["approved_qty"] >= 1
    row = qr.exposure_row("SLV", 1, 37.10)
    assert row["group"] == "OTHER" and row["factor"] == 1


# ------------------------------------------------------------------ autotrader
def _market(clock, price, ticker=BIG):
    return {"clock": clock, "quotes": {ticker: {"intraday_price": price}},
            "techs": {}, "daily": {}, "snapshot": {}, "vix": 18.0}


def _cand(ticker=BIG):
    return {"ticker": ticker, "source": "DIP_SWING_DAILY",
            "signal": {"rsi2": 12.0, "pct_b": 0.05, "rvol": 1.4, "price": 717.0},
            "why": "테스트 과매도 신호", "atr_pct": 1.0}


@pytest.fixture()
def big_trader(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    policy = copy.deepcopy(autotrader.execution_policy.load_policy())
    policy.setdefault("autonomous_execution", {})
    policy["universe"] = list(policy.get("universe") or []) + [BIG]
    t = AutonomousTrader(policy=policy, router=FakeRouter(account={"found": False}),
                         dry_run=False, market=_market(clock_at("01:00"), 717.0),
                         base_dir=str(tmp_path))
    t._brackets_path = brackets_path
    return t


def _clean_brackets(tmp_trader):
    """실전 계좌와 같게 SGOV 캐리만 남긴다(NVDA 청산 완료)."""
    path = tmp_trader[1]
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.pop("NVDA", None)
    doc["SGOV"] = {"bought_qty": 1, "entry_price": 100.53, "note": "CARRY_PARKING",
                   "carry_parking": True}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_autotrader_buys_one_share_of_700_dollar_stock(big_trader, tmp_trader):
    brackets = _clean_brackets(tmp_trader)
    st = big_trader.load_state("2026-11-17")
    big_trader.entry_candidates = lambda *a, **k: [_cand()]
    led = {"equity_usd": 881.24, "cash_usd": 780.56}
    actions = big_trader.entries(st, brackets, _market(clock_at("01:00"), 717.0), led)
    assert len(actions) == 1, [e for e in big_trader.events if e["kind"].startswith("ENTRY")]
    buy = actions[0]
    assert buy["side"] == "BUY" and buy["qty"] == 1
    assert buy["sizing"]["unit_share"] is True
    assert buy["sizing"]["budget_before_usd"] == pytest.approx(220.0, abs=0.01)
    assert buy["sizing"]["notional_usd"] == pytest.approx(717.0, rel=0.01)
    # 매수 후에도 1주 안전마진($40)이 남는다.
    assert buy["sizing"]["cash_after_usd"] >= 40.0
    call = big_trader.router.calls[0]
    assert call["ticker"] == BIG and call["qty"] == 1 and call["side"] == "BUY"


def test_unit_share_path_survives_vix_decay_but_records_it(big_trader, tmp_trader):
    """VIX 계수는 '수량'을 줄이는 값이다 — 1주 미만으로 줄일 수 없으므로 1주 경로는 유지되고,
    축소 전 예산을 sizing에 남긴다(VIX ≥ 28 하드 차단은 별도로 유지)."""
    brackets = _clean_brackets(tmp_trader)
    st = big_trader.load_state("2026-11-17")
    big_trader.entry_candidates = lambda *a, **k: [_cand()]
    market = _market(clock_at("01:00"), 717.0)
    market["vix"] = 25.0                      # 계수 0.5 → 기존 예산 $150 사정
    actions = big_trader.entries(st, brackets, market,
                                 {"equity_usd": 881.24, "cash_usd": 780.56})
    assert len(actions) == 1 and actions[0]["qty"] == 1
    assert actions[0]["sizing"]["budget_before_usd"] <= 150.0 + 0.01
    assert actions[0]["sizing"]["unit_share"] is True


def test_autotrader_still_refuses_unit_price_above_cap(big_trader, tmp_trader):
    brackets = _clean_brackets(tmp_trader)
    st = big_trader.load_state("2026-11-17")
    big_trader.entry_candidates = lambda *a, **k: [_cand()]
    led = {"equity_usd": 881.24, "cash_usd": 780.56}
    actions = big_trader.entries(st, brackets, _market(clock_at("01:00"), 1087.0), led)
    assert actions == []
    assert any("수량 0" in str(e.get("reason")) or "예산" in str(e.get("reason"))
               for e in big_trader.events if e["kind"] in ("ENTRY_SKIP",))


def test_unit_share_path_is_limited_to_prices_above_the_per_trade_cap(big_trader, tmp_trader):
    """$220 이하 종목은 VIX 감쇠로 1주도 못 사는 경우에도 종전대로 거부된다(범위 축소)."""
    brackets = _clean_brackets(tmp_trader)
    st = big_trader.load_state("2026-11-17")
    big_trader.entry_candidates = lambda *a, **k: [
        {"ticker": "SOXL", "source": "DIP_SWING_DAILY",
         "signal": {"rsi2": 12.0, "pct_b": 0.05, "rvol": 1.4}, "why": "테스트", "atr_pct": 10.0}]
    market = _market(clock_at("01:00"), 160.0, ticker="SOXL")
    market["vix"] = 25.0                              # 계수 0.5 → 예산 $150 < 1주 $160
    actions = big_trader.entries(st, brackets, market,
                                 {"equity_usd": 881.24, "cash_usd": 780.56})
    assert actions == []


def test_autotrader_keeps_normal_budget_for_cheap_names(big_trader, tmp_trader):
    """$220 이하 종목은 종전 경로 그대로 — 1주 경로가 예산을 부풀리지 않는다."""
    brackets = _clean_brackets(tmp_trader)
    brackets["SOXL"] = {}
    brackets.pop("SOXL")
    st = big_trader.load_state("2026-11-17")
    big_trader.entry_candidates = lambda *a, **k: [
        {"ticker": "SOXL", "source": "DIP_SWING_DAILY",
         "signal": {"rsi2": 12.0, "pct_b": 0.05, "rvol": 1.4}, "why": "테스트",
         "atr_pct": 10.0}]
    led = {"equity_usd": 881.24, "cash_usd": 780.56}
    actions = big_trader.entries(st, brackets, _market(clock_at("01:00"), 50.0, ticker="SOXL"), led)
    assert len(actions) == 1
    assert actions[0]["sizing"].get("unit_share") is None
    assert actions[0]["sizing"]["notional_usd"] <= 220.0
