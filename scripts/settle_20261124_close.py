#!/usr/bin/env python3
"""11-24(화) 미국 정규장 마감 정산 (2026-11-25 06:00 KST, NH Open API 실측).

입력은 추정치가 아니라 GitHub Actions `nh_trade_dispatch.yml` `mode=test_balance`
(run 45486595193)가 돌려준 NH Open API 원본 응답이다. 이 스크립트는
  * data/daily_trading_calendar.json 의 2026-11-24 레코드와 monthly_summary
  * data/live_portfolio.json · docs/live_portfolio.json
  * data/nh_account_snapshot.json (감사용 원본 스냅샷)
를 실측값으로만 갱신한다. 화면(index.html·cube_report·위젯)은 이어서
`scripts/sync_live_portfolio.py` 와 `scripts/build_full_cube.py` 가 원장에서 다시 만든다.

브로커 총자산 항등식(이번 정산에서 확인):
    fc_aet_amt = fc_dca + fc_ny_stl_xcl_amt + fc_eal_amt
    881.21     = 727.70 + 52.86             + 100.65      (잔차 0.00)

당일 체결(11-24 ET 세션):
  - 체결 없음(0건). 원격 워크플로(intervene_trade.yml / nh_trade_dispatch.yml order_type)
    미반영 상태로 주간거래(63)·프리장(61) 신규 진입 미실행.
  - 11-23 SQQQ 매도 순액 $52.86은 T+2 미결제 정산금으로 유지.

대조 항등식(총자산 변동 분해, 잔차 0.00):
    +0.01 = 실현 0.00 + 평가변동 (0.12 - 0.11 = +0.01) - 수수료 0.00
"""
import json
import os
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- NH Open API 실측 (2026-11-25 06:00 KST, workflow run 45486595193) ----
BROKER = {
    "fetched_at_kst": "2026-11-25 06:00 KST",
    "workflow_run_id": 45486595193,
    "rsp_cd": "00166",
    "rsp_msg": "조회가 완료되었습니다.",
    "equity_usd": 881.21,          # fc_aet_amt
    "equity_krw": 1186649,         # tot_aet_amt
    "cash_usd": 727.70,            # fc_dca (외화예수금 = 주문가능 현금)
    "cash_krw": 979920,            # krw_dca
    "unsettled_usd": 52.86,        # fc_ny_stl_xcl_amt (11-23 SQQQ 매도 순액, T+2 대기)
    "unsettled_krw": 71181,        # krw_ny_stl_xcl_amt
    "eval_usd": 100.65,            # fc_eal_amt
    "eval_krw": 135548,            # eal_amt_sum
    "cost_usd": 100.53,            # fc_abk_amt
    "cost_krw": 135042,            # abk_amt
    "unreal_usd": 0.12,            # fc_eal_pls_amt
    "unreal_krw": 506,             # eal_pls_sum_amt
    "return_pct": 0.11,            # pft_rt
    "fx_settle": 1346.6,           # tdt_sby_bse_xcg_rt (결제 기준환율)
    "positions": {
        "SGOV": {"qty": 1, "avg": 100.53, "mark": 100.65, "eval": 100.65,
                 "unreal": 0.12, "unreal_krw": 506, "ret": 0.11, "fx_purchase": 1343.30,
                 "kind": "1주 보유 — 캐리 파킹 (11-24 종가 $100.65)"},
    },
    "executions": {                # /gbstock/inquiry/v1/unexecuted (orr_dt별)
        "20261125": 0,
        "20261124": 0,
        "20261123": 1,
    },
}

FILLS = []

# ---- 누적 실현 손익 (변동 없음) ----
REALIZED_DAY_USD = 0.00
REALIZED_DAY_KRW = 0
REALIZED_USD = -17.43
REALIZED_KRW = round(REALIZED_USD * BROKER["fx_settle"])
TRADES, WINS, LOSSES, WIN_RATE = 11, 6, 5, round(6 / 11 * 100, 1)

# ---- 전일 확정값 (11-24 06:01 KST 실측 — 11-23 세션) ----
PREV_EQUITY_USD, PREV_EQUITY_KRW = 881.20, 1178432
PREV_UNREAL_USD = 0.11

INITIAL_USD, INITIAL_KRW = 900.18, 1209215
FEE_TOTAL_USD = 0.00


def now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")


def assert_identity() -> None:
    lhs = round(BROKER["cash_usd"] + BROKER["unsettled_usd"] + BROKER["eval_usd"], 2)
    assert lhs == BROKER["equity_usd"], f"브로커 항등식 불일치: {lhs} != {BROKER['equity_usd']}"
    pos_sum = round(sum(p["eval"] for p in BROKER["positions"].values()), 2)
    assert pos_sum == BROKER["eval_usd"], f"종목 평가 합계 불일치: {pos_sum}"
    unreal = round(sum(p["unreal"] for p in BROKER["positions"].values()), 2)
    assert unreal == BROKER["unreal_usd"], f"평가손익 합계 불일치: {unreal}"
    delta = round(REALIZED_DAY_USD + (BROKER["unreal_usd"] - PREV_UNREAL_USD) - FEE_TOTAL_USD, 2)
    day = round(BROKER["equity_usd"] - PREV_EQUITY_USD, 2)
    assert delta == day, f"총자산 변동 분해 불일치: {delta} != {day}"


def build_positions() -> list:
    out = []
    for t, p in BROKER["positions"].items():
        out.append({
            "ticker": t,
            "qty": p["qty"],
            "avg_price": p["avg"],
            "market_price": p["mark"],
            "market_value_usd": p["eval"],
            "unrealized_usd": p["unreal"],
            "return_pct": p["ret"],
        })
    return out


def update_calendar() -> dict:
    path = os.path.join(BASE_DIR, "data", "daily_trading_calendar.json")
    with open(path, "r", encoding="utf-8") as f:
        cal = json.load(f)

    cash_total_usd = round(BROKER["cash_usd"] + BROKER["unsettled_usd"], 2)
    cash_total_krw = BROKER["cash_krw"] + BROKER["unsettled_krw"]
    net_usd = round(REALIZED_USD + BROKER["unreal_usd"], 2)
    net_krw = REALIZED_KRW + BROKER["unreal_krw"]
    day_usd = round(BROKER["equity_usd"] - PREV_EQUITY_USD, 2)
    day_krw = BROKER["equity_krw"] - PREV_EQUITY_KRW
    total_usd = round(BROKER["equity_usd"] - INITIAL_USD, 2)
    total_krw = BROKER["equity_krw"] - INITIAL_KRW

    cal["daily_records"]["2026-11-24"] = {
        "day": 24,
        "is_trading_day": True,
        "future": False,
        "session_date_us": "2026-11-24",
        "balance_krw": BROKER["equity_krw"],
        "equity_usd": BROKER["equity_usd"],
        "profit_usd": REALIZED_DAY_USD,
        "profit_krw": REALIZED_DAY_KRW,
        "return_pct": 0.0,
        "trades_count": 0,
        "trades": [],
        "positions": build_positions(),
        "positions_value_usd": BROKER["eval_usd"],
        "cash_buffer_usd": BROKER["cash_usd"],
        "cash_buffer_krw": BROKER["cash_krw"],
        "cash_buffer_pct": round(BROKER["cash_usd"] / BROKER["equity_usd"] * 100, 1),
        "cash_pending_settlement_usd": BROKER["unsettled_usd"],
        "cash_pending_settlement_krw": BROKER["unsettled_krw"],
        "cash_total_usd": cash_total_usd,
        "cash_total_krw": cash_total_krw,
        "cash_total_pct": round(cash_total_usd / BROKER["equity_usd"] * 100, 1),
        "unrealized_pnl_usd": BROKER["unreal_usd"],
        "unrealized_pnl_krw": BROKER["unreal_krw"],
        "net_total_pnl_usd": net_usd,
        "net_total_pnl_krw": net_krw,
        "day_pnl_usd": day_usd,
        "day_pnl_krw": day_krw,
        "total_pnl_usd": total_usd,
        "total_pnl_krw": total_krw,
        "fees_usd": FEE_TOTAL_USD,
        "status": "마감 정산 확정 — 체결 없음(0건), 보유 SGOV 1주 ($100.65)",
        "note": (
            "미국 11-24(화) 세션 마감 정산(NH Open API 실측 2026-11-25 06:00 KST, run 45486595193). "
            "당일 체결 0건(원격 워크플로 order_type 63/61 미반영 상태로 주간거래·프리장 신규 진입 미실행). "
            "11-23 SQQQ 매도 순액 $52.86은 미결제 정산금(T+2 대기)으로 유지. "
            f"총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) = 외화예수금 ${BROKER['cash_usd']:,.2f}"
            f"({BROKER['cash_krw']:,}원, {round(BROKER['cash_usd'] / BROKER['equity_usd'] * 100, 1)}%) + "
            f"미결제 정산금 ${BROKER['unsettled_usd']:,.2f}({BROKER['unsettled_krw']:,}원) + 평가 ${BROKER['eval_usd']:,.2f}; "
            "항등식 잔차 $0.00. 보유는 SGOV 1주($100.65, 평가 +$0.12). "
            f"누적 실현 -${abs(REALIZED_USD):,.2f}({REALIZED_KRW:,}원, {TRADES}전 {WINS}승 {LOSSES}패, 승률 {WIN_RATE}%)."
        ),
    }

    ms = cal["monthly_summary"]
    ms.update({
        "total_profit_usd": REALIZED_USD,
        "total_profit_krw": REALIZED_KRW,
        "monthly_return_pct": round(REALIZED_USD / INITIAL_USD * 100, 2),
        "total_unrealized_usd": BROKER["unreal_usd"],
        "net_total_pnl_usd": net_usd,
        "win_rate_pct": WIN_RATE,
        "total_trades": TRADES,
        "wins": WINS,
        "losses": LOSSES,
        "current_balance_krw": BROKER["equity_krw"],
        "current_balance_usd": BROKER["equity_usd"],
        "cash_buffer_usd": BROKER["cash_usd"],
        "cash_buffer_krw": BROKER["cash_krw"],
        "cash_pending_settlement_usd": BROKER["unsettled_usd"],
        "cash_pending_settlement_krw": BROKER["unsettled_krw"],
        "fx_rate": BROKER["fx_settle"],
        "fx_rate_source": "NH Open API 적용환율 tdt_sby_bse_xcg_rt (2026-11-25 06:00 KST 마감 정산)",
        "account_status": (
            f"실전 11일차(11-24 화 세션) 마감 정산 확정(2026-11-25 06:00 KST): 체결 0건. "
            f"보유 SGOV 1주($100.65, 평가 +$0.12). "
            f"총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) · 외화예수금 ${BROKER['cash_usd']:,.2f}"
            f"({BROKER['cash_krw']:,}원, {round(BROKER['cash_usd'] / BROKER['equity_usd'] * 100, 1)}%) + "
            f"미결제 정산금 ${BROKER['unsettled_usd']:,.2f}({BROKER['unsettled_krw']:,}원). "
            f"누적 실현 -${abs(REALIZED_USD):,.2f}({TRADES}전 {WINS}승 {LOSSES}패, 승률 {WIN_RATE}%), "
            f"평가 +${BROKER['unreal_usd']:,.2f}, 총자산 손익 {total_usd:+,.2f}달러({total_krw:+,}원)."
        ),
    })

    for rel in ("data/daily_trading_calendar.json", "docs/daily_trading_calendar.json"):
        p = os.path.join(BASE_DIR, rel)
        if os.path.isdir(os.path.dirname(p)):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cal, f, ensure_ascii=False, indent=2)
    return {"net_usd": net_usd, "net_krw": net_krw, "day_usd": day_usd,
            "day_krw": day_krw, "total_usd": total_usd, "total_krw": total_krw,
            "cash_total_usd": cash_total_usd, "cash_total_krw": cash_total_krw}


def update_live_portfolio(calc: dict) -> None:
    pos_now = build_positions()
    for p in pos_now:
        src = BROKER["positions"][p["ticker"]]
        p["name"] = "아이셰어즈 0-3개월 미국 국채 ETF"
        p["price_as_of"] = "2026-11-24"
        p["broker_mark_price"] = p["market_price"]
        p["quote_last"] = p["market_price"]
        p["unrealized_krw"] = src["unreal_krw"]
        p["status"] = src["kind"]
        p["risk_status"] = None
        p["bracket"] = None

    payload = {
        "api_status": "CONNECTED",
        "broker": "NH투자증권 나무 (Open API)",
        "account_no": "202-05-134849",
        "last_synced": now_kst(),
        "session_phase": "POST_MARKET",
        "price_basis": "11-24 정규장 종가 (NH fc_sec_end_pr)",
        "price_source": "NH Open API 종가(fc_sec_end_pr) 우선, 없으면 Yahoo 마지막 시세",
        "fx_rate": BROKER["fx_settle"],
        "fx_source": "NH Open API 적용환율(tdt_sby_bse_xcg_rt) — 2026-11-25 06:00 KST 마감 정산",
        "initial_funding_krw": INITIAL_KRW,
        "initial_funding_usd": INITIAL_USD,
        "current_total_equity_usd": BROKER["equity_usd"],
        "current_total_equity_krw": BROKER["equity_krw"],
        "cash_buffer_usd": BROKER["cash_usd"],
        "cash_buffer_krw": BROKER["cash_krw"],
        "cash_buffer_pct": round(BROKER["cash_usd"] / BROKER["equity_usd"] * 100, 1),
        "cash_buffer_note": ("외화예수금(fc_dca) = 주문가능 현금. "
                             "11-23 SQQQ 매도 순액 $52.86은 미결제 정산금으로 T+2 결제 대기."),
        "cash_deposit_usd": BROKER["cash_usd"],
        "cash_deposit_krw": BROKER["cash_krw"],
        "cash_pending_settlement_usd": BROKER["unsettled_usd"],
        "cash_pending_settlement_krw": BROKER["unsettled_krw"],
        "cash_total_usd": calc["cash_total_usd"],
        "cash_total_krw": calc["cash_total_krw"],
        "cash_total_pct": round(calc["cash_total_usd"] / BROKER["equity_usd"] * 100, 1),
        "account_sync": {
            "source": "NH Open API POST /gbstock/inquiry/v1/balance",
            "request_fields": "act_no, qut_iqr_dit_cd=9, fc_sec_trd_nat_cd=200, cur_cd=USD",
            "rsp_cd": BROKER["rsp_cd"],
            "rsp_msg": BROKER["rsp_msg"],
            "synced_at_kst": BROKER["fetched_at_kst"],
            "workflow_run_id": BROKER["workflow_run_id"],
            "executions_by_orr_dt": BROKER["executions"],
        },
        "broker_identity": {
            "formula": "fc_aet_amt = fc_dca + fc_ny_stl_xcl_amt + fc_eal_amt",
            "cash_usd": BROKER["cash_usd"],
            "pending_settlement_usd": BROKER["unsettled_usd"],
            "positions_eval_usd": BROKER["eval_usd"],
            "equity_usd": BROKER["equity_usd"],
            "residual_usd": round(BROKER["equity_usd"] - (BROKER["cash_usd"] + BROKER["unsettled_usd"] + BROKER["eval_usd"]), 2),
            "prev_residual_usd": 0.0,
            "prev_residual_explained": "전일(11-24 06:01)에도 잔차 0.00",
        },
        "fills_confirmed": FILLS,
        "realized_pnl_usd": REALIZED_USD,
        "realized_pnl_krw": REALIZED_KRW,
        "unrealized_pnl_usd": BROKER["unreal_usd"],
        "unrealized_pnl_krw": BROKER["unreal_krw"],
        "unrealized_recomputed_usd": round(sum(p["unrealized_usd"] for p in pos_now), 2),
        "unrealized_source": "NH 계좌 평가손익(fc_eal_pls_amt) 기준 — 종가 재평가",
        "net_total_pnl_usd": calc["net_usd"],
        "net_total_pnl_krw": calc["net_krw"],
        "day_pnl_usd": calc["day_usd"],
        "day_pnl_krw": calc["day_krw"],
        "total_pnl_usd": calc["total_usd"],
        "total_pnl_krw": calc["total_krw"],
        "fees_usd": FEE_TOTAL_USD,
        "win_rate_pct": WIN_RATE,
        "trades_summary": f"{TRADES}전 {WINS}승 {LOSSES}패 (승률 {WIN_RATE}%)",
        "positions_market_value_usd": BROKER["eval_usd"],
        "positions": pos_now,
        "recent_trades": FILLS,
        "inverse_hedging_ready": True,
        "inverse_tickers": ["SOXS", "SQQQ"],
        "ledger_consistency": {"checked_at": None},
    }

    for rel in ("data/live_portfolio.json", "docs/live_portfolio.json"):
        p = os.path.join(BASE_DIR, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    snap = {
        "fetched_at_kst": BROKER["fetched_at_kst"],
        "workflow_run_id": BROKER["workflow_run_id"],
        "found": True,
        "equity_usd": BROKER["equity_usd"],
        "equity_krw": BROKER["equity_krw"],
        "cash_usd": BROKER["cash_usd"],
        "cash_krw": BROKER["cash_krw"],
        "pending_settlement_usd": BROKER["unsettled_usd"],
        "pending_settlement_krw": BROKER["unsettled_krw"],
        "positions_eval_usd": BROKER["eval_usd"],
        "positions_cost_usd": BROKER["cost_usd"],
        "unrealized_usd": BROKER["unreal_usd"],
        "unrealized_krw": BROKER["unreal_krw"],
        "return_pct": BROKER["return_pct"],
        "fx_settle": BROKER["fx_settle"],
        "positions": BROKER["positions"],
        "recent_executions_counts": BROKER["executions"],
        "fills": FILLS,
        "realized_cumulative_usd": REALIZED_USD,
        "realized_cumulative_krw": REALIZED_KRW,
        "rsp_cd": BROKER["rsp_cd"],
        "rsp_msg": BROKER["rsp_msg"],
    }
    with open(os.path.join(BASE_DIR, "data", "nh_account_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)


def main() -> None:
    assert_identity()
    calc = update_calendar()
    update_live_portfolio(calc)
    print(f"✅ 11-24 정산 반영 | 총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) "
          f"| 현금 ${BROKER['cash_usd']:,.2f}+미결제 ${BROKER['unsettled_usd']:,.2f} "
          f"| 평가 ${BROKER['eval_usd']:,.2f}({BROKER['unreal_usd']:+,.2f}) "
          f"| 당일 {calc['day_usd']:+,.2f}달러({calc['day_krw']:+,}원) "
          f"| 누적 실현 ${REALIZED_USD:,.2f} / 순 {calc['net_usd']:+,.2f}")


if __name__ == "__main__":
    main()
