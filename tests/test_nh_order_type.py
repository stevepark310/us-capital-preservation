"""NH 해외주식 호가유형(프리마켓 61·애프터마켓 62) 지원과 안전장치 (네트워크 없음).

확인하는 것
-----------
1. `place_order(order_type=...)`가 NH 코드('00'|'61'|'62')를 payload에 정확히 싣는다.
2. 기본값은 기존 동작 그대로 '00'이다(현행 워크플로 호환).
3. 모르는 값은 조용히 '00'으로 떨어지지 않고 ValueError로 막힌다.
4. `config/premarket_policy.json`이 enabled=false인 동안 61/62 주문은 디스패치 전에 막힌다.
5. 워크플로 디스패치 명령은 order_type이 기본값일 때 입력을 붙이지 않는다.
"""
import json

import pytest

from execution import order_bridge as ob
from execution import nh_trader as nt
from execution.nh_trader import NHBroker, ORDER_TYPE_CODES, normalize_order_type


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch, tmp_path):
    """테스트가 .env 자격증명으로 토큰을 발급하거나 실전 주문 로그를 건드리지 않도록 격리."""
    monkeypatch.setattr(nt, "_load_env_fallback", lambda: None)
    for key in ("NH_APP_KEY", "NH_APP_SECRET", "NH_ACT_NO"):
        monkeypatch.delenv(key, raising=False)
    # 실전 감사 로그·디스패치 상태를 오염시키지 않는다
    monkeypatch.setattr(ob, "ORDERS_LOG", str(tmp_path / "live_order_responses.jsonl"))
    monkeypatch.setattr(ob, "DISPATCH_STATE", str(tmp_path / "order_dispatch_state.json"))
    monkeypatch.setattr(ob, "ACCOUNT_SNAPSHOT", str(tmp_path / "nh_account_snapshot.json"))


# --------------------------------------------------------------- 코드 정규화
def test_normalize_accepts_codes_and_names():
    assert normalize_order_type("00") == "00"
    assert normalize_order_type("61") == "61"
    assert normalize_order_type("62") == "62"
    assert normalize_order_type("premarket") == "61"
    assert normalize_order_type("PRE-MARKET") == "61"
    assert normalize_order_type("프리마켓") == "61"
    assert normalize_order_type("after_hours") == "62"
    assert normalize_order_type(None) == "00"
    assert normalize_order_type("") == "00"


def test_normalize_priority_and_rejection():
    # 원본 API 필드명이 주어지면 그쪽을 우선한다
    assert normalize_order_type("00", ahi_nmn_pr_tp_cd="61") == "61"
    for bad in ("61x", "market", "99", "지정가"):
        with pytest.raises(ValueError):
            normalize_order_type(bad)


def test_order_type_codes_table():
    assert set(ORDER_TYPE_CODES) == {"00", "61", "62"}
    assert ORDER_TYPE_CODES["61"][0] == "premarket_limit"


# ----------------------------------------------------------------- 드라이런
def test_dry_run_reports_order_type_and_default_is_regular():
    b = NHBroker(dry_run=True)
    default = b.place_order("SOXL", "BUY", 1, 50.6)
    assert default["ahi_nmn_pr_tp_cd"] == "00" and default["order_type"] == "regular_limit"

    pre = b.place_order("SOXL", "BUY", 1, 50.6, order_type="61")
    assert pre["ahi_nmn_pr_tp_cd"] == "61" and pre["order_type"] == "premarket_limit"
    assert pre["dry_run"] is True

    after = b.place_order("SOXL", "SELL", 1, 51.0, ahi_nmn_pr_tp_cd="62")
    assert after["ahi_nmn_pr_tp_cd"] == "62"
    assert after["side"] == "SELL"


def test_dry_run_keeps_validation():
    b = NHBroker(dry_run=True)
    with pytest.raises(ValueError):
        b.place_order("SOXL", "BUY", 1, 0.0, order_type="61")     # 지정가 가격 필수
    with pytest.raises(ValueError):
        b.place_order("SOXL", "BUY", 0, 50.0, order_type="61")     # 수량
    with pytest.raises(ValueError):
        b.place_order("SOXL", "SHORT", 1, 50.0, order_type="61")   # 방향


# ------------------------------------------------------------- 실주문 payload
@pytest.fixture()
def captured_broker(monkeypatch):
    b = NHBroker(dry_run=False)
    b.access_token = "test-token"
    calls = []

    def fake_call(path, payload):
        calls.append({"path": path, "payload": payload})
        return {"rsp_cd": "00171", "rsp_msg": "ok", "Output_0": {"orr_no": "T1"}}

    monkeypatch.setattr(b, "_call_api", fake_call)
    return b, calls


@pytest.mark.parametrize("code,expected", [("00", "00"), ("61", "61"), ("62", "62")])
def test_payload_carries_order_type(captured_broker, code, expected):
    b, calls = captured_broker
    b.place_order("SOXS", "BUY", 1, 58.77, order_type=code)
    assert calls[-1]["payload"]["ahi_nmn_pr_tp_cd"] == expected
    assert calls[-1]["payload"]["fc_orr_uit_pr"] == 58.77
    assert calls[-1]["payload"]["wtm_cur_knd_cd"] == "1"
    assert calls[-1]["path"] == "/gbstock/order/v1/buy"


def test_sell_payload_has_no_currency_field(captured_broker):
    b, calls = captured_broker
    b.place_order("SOXL", "SELL", 2, 49.07, order_type="61")
    assert calls[-1]["path"] == "/gbstock/order/v1/sell"
    assert "wtm_cur_knd_cd" not in calls[-1]["payload"]
    assert calls[-1]["payload"]["ahi_nmn_pr_tp_cd"] == "61"


# ----------------------------------------------------------------- 정책 게이트
def test_premarket_policy_defaults_to_blocked(tmp_path, monkeypatch):
    pol = tmp_path / "premarket_policy.json"
    pol.write_text(json.dumps({"enabled": False, "reason": "백테스트 미승인"}), encoding="utf-8")
    monkeypatch.setattr(ob, "PREMARKET_POLICY", str(pol))
    r = ob.OrderRouter(dry_run=True)
    res = r.place("SOXL", "BUY", 1, 50.6, "test", dry_run=True, order_type="61")
    assert res["status"] == "BLOCKED_POLICY"
    assert "백테스트" in res["error"]
    # 정규장 주문은 영향 없음
    ok = r.place("SOXL", "BUY", 1, 50.6, "test", dry_run=True)
    assert ok["status"] == "DRY_RUN"


def test_premarket_policy_missing_file_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "PREMARKET_POLICY", str(tmp_path / "nope.json"))
    r = ob.OrderRouter(dry_run=True)
    res = r.place("SOXS", "BUY", 1, 58.7, "test", dry_run=True, order_type="62")
    assert res["status"] == "BLOCKED_POLICY"


def test_repo_policy_file_is_approved_for_manual_intervention_only():
    """저장소 정책 파일 — 2026-11-21 18:41 승인(안 A): 프리장 61은 수동·지시 개입만."""
    with open(ob.PREMARKET_POLICY, "r", encoding="utf-8") as f:
        pol = json.load(f)
    assert pol["enabled"] is True
    assert pol["manual_intervention_only"] is True
    assert pol["approved_by"] and pol["approved_at_kst"]
    assert set(pol["supported_order_types"]) == {"00", "61", "62"}


# --------------------------------------------------------------- 디스패치 명령
def test_build_command_omits_default_order_type():
    r = ob.OrderRouter(repo="stevepark310/us-capital-preservation", dry_run=True)
    plain = " ".join(r.build_command("buy", "SOXL", 1, 50.6))
    assert "order_type" not in plain          # 현행 워크플로에 없는 입력은 보내지 않는다
    explicit = " ".join(r.build_command("buy", "SOXL", 1, 50.6, order_type="00"))
    assert "order_type" not in explicit


@pytest.mark.parametrize("code", ["61", "62"])
def test_build_command_includes_non_default_order_type(code):
    r = ob.OrderRouter(repo="stevepark310/us-capital-preservation", dry_run=True)
    cmd = " ".join(r.build_command("buy", "SOXS", 1, 58.77, order_type=code))
    assert f"order_type={code}" in cmd


def test_idempotency_key_separates_order_windows():
    regular = ob.OrderRouter.idempotency_key("2026-11-18", "SOXL", "BUY", 1, 50.60)
    assert regular == "2026-11-18|SOXL|BUY|1|50.60"
    pre = ob.OrderRouter.idempotency_key("2026-11-18", "SOXL", "BUY", 1, 50.60, "61")
    assert pre.endswith("|61") and pre != regular


# ------------------------------------------------------------- 자동매매 연동
def test_autotrader_routes_premarket_only_when_policy_approved():
    """프리장 주문 경로는 **정책 승인 뒤에만** 열린다(2026-11-21 지시).

    종전에는 `core/autotrader.py`가 프리장 주문을 아예 몰랐다(소스 검사로 고정).
    이제는 세션 계획(`core/session_routing`)이 판정하고, 스위치가 꺼져 있거나
    사용자 승인 기록이 없으면 프리장 호가유형은 None이다 — 코드가 스스로
    프리장을 여는 경로가 없다는 점은 아래 두 번째 단언이 지킨다.
    """
    from core import autotrader as at, session_routing

    closed = at.AutonomousTrader(policy={"enabled": True}, dry_run=True, router=object())
    assert closed.order_type({"phase": "PRE_MARKET"}) is None
    assert closed.order_type({"phase": "REGULAR"}) == "00"

    switched_only = session_routing.status({"enabled": True})
    assert "PRE_MARKET" not in switched_only["phases"]          # 승인 기록 없는 스위치
    no_approval = at.AutonomousTrader(policy={"enabled": True}, dry_run=True, router=object(),
                                      session_plan=switched_only)
    assert no_approval.order_type({"phase": "PRE_MARKET"}) is None

    approved_plan = session_routing.status({"enabled": True, "approved_by": "박동훈",
                                           "approved_at_kst": "2026-11-22 09:00:00"})
    trader = at.AutonomousTrader(policy={"enabled": True}, dry_run=True, router=object(),
                                 session_plan=approved_plan)
    assert trader.order_type({"phase": "PRE_MARKET"}) == "61"
    assert trader.order_type({"phase": "REGULAR"}) == "00"
    assert trader.order_type({"phase": "OFF_HOURS"}) is None
