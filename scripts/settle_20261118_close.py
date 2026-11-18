#!/usr/bin/env python3
"""11-18(수) 미국 정규장 마감 정산 (2026-11-19 06:01 KST, NH Open API 실측).

입력은 추정치가 아니라 GitHub Actions `nh_trade_dispatch.yml` `mode=test_balance`
(run 44397133539)가 돌려준 NH Open API 원본 응답이다. 이 스크립트는
  * data/daily_trading_calendar.json 의 2026-11-18 레코드와 monthly_summary
  * data/live_portfolio.json · docs/live_portfolio.json
  * docs/daily_trading_calendar.json
를 실측값으로만 갱신한다. 화면(cube_report·index.html·위젯)은 이어서
`scripts/update_feed.py` 와 `scripts/build_full_cube.py` 가 원장에서 다시 만든다.

브로커 총자산 항등식(이번 정산에서 확인):
    fc_aet_amt = fc_dca + fc_ny_stl_xcl_amt + fc_eal_amt
    878.60     = 426.63 + 200.23            + 251.74      (잔차 0.00)

전일(11-18 06:01) -$9.15 잔차도 같은 항등식의 fc_ny_stl_xcl_amt(-9.15)였다:
    883.50 = 636.01 + (-9.15) + 256.64
즉 원장이 '예수금 + 평가'만 더해 총자산과 비교했기 때문에 생긴 항목 차이이며,
계좌 금액 오류가 아니었다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- NH Open API 실측 (2026-11-19 06:01 KST, workflow run 44397133539) ----
BROKER = {
    "fetched_at_kst": "2026-11-19 06:01 KST",
    "workflow_run_id": 44397133539,
    "rsp_cd": "00166",
    "rsp_msg": "조회가 완료되었습니다.",
    "equity_usd": 878.60,          # fc_aet_amt
    "equity_krw": 1176272,         # tot_aet_amt
    "cash_usd": 426.63,            # fc_dca (외화예수금 = 주문가능 현금)
    "cash_krw": 571172,            # krw_dca
    "unsettled_usd": 200.23,       # fc_ny_stl_xcl_amt (미결제 정산금, 11-17 매도대금)
    "unsettled_krw": 268067,       # krw_ny_stl_xcl_amt
    "eval_usd": 251.74,            # fc_eal_amt
    "eval_krw": 337033,            # eal_amt_sum
    "cost_usd": 273.83,            # fc_abk_amt
    "cost_krw": 367836,            # abk_amt
    "unreal_usd": -22.09,          # fc_eal_pls_amt
    "unreal_krw": -30803,          # eal_pls_sum_amt
    "return_pct": -8.06,           # pft_rt
    "fx_settle": 1338.8,           # tdt_sby_bse_xcg_rt (결제 기준환율)
    "fx_purchase": 1343.3,         # phs_xcg_rt (매입환율)
    "positions": {
        "NVDA": {"qty": 1, "avg": 173.30, "mark": 151.15, "eval": 151.15,
                 "unreal": -22.15, "unreal_krw": -30435, "ret": -12.78,
                 "kind": "1주 보유 — 11-18 종가 $151.15, 잠금 브래킷 TP $173.0 / SL $150.0 유지 (당일 저가 $150.40, SL 미터치)"},
        "SGOV": {"qty": 1, "avg": 100.53, "mark": 100.59, "eval": 100.59,
                 "unreal": 0.06, "unreal_krw": -368, "ret": 0.05,
                 "kind": "1주 보유 — 캐리 파킹"},
    },
    "executions": {                # /gbstock/inquiry/v1/unexecuted (orr_dt별)
        "20261119": 0,
        "20261118": 0,             # 11-18 미국 정규장: 체결·주문 0건
        "20261117": 2,             # 11-17 세션 손절 2건(TQQQ 175399 / SOXL 192126)
    },
}

# ---- 누적 실현 손익 (기존 확정 원장, 변경 없음) ----
REALIZED_USD = 1.85
REALIZED_KRW = 2470
TRADES, WINS, LOSSES, WIN_RATE = 9, 5, 4, 55.6

# ---- 전일 확정값 (11-18 06:01 KST 실측: 883.50 = 636.01 - 9.15 + 256.64) ----
PREV_EQUITY_USD, PREV_EQUITY_KRW = 883.50, 1179485

INITIAL_USD, INITIAL_KRW = 900.18, 1209215


def now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")


def assert_identity() -> None:
    lhs = round(BROKER["cash_usd"] + BROKER["unsettled_usd"] + BROKER["eval_usd"], 2)
    assert lhs == BROKER["equity_usd"], f"브로커 항등식 불일치: {lhs} != {BROKER['equity_usd']}"
    pos_sum = round(sum(p["eval"] for p in BROKER["positions"].values()), 2)
    assert pos_sum == BROKER["eval_usd"], f"종목 평가 합계 불일치: {pos_sum}"
    unreal = round(sum(p["unreal"] for p in BROKER["positions"].values()), 2)
    assert unreal == BROKER["unreal_usd"], f"평가손익 합계 불일치: {unreal}"


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

    cal["daily_records"]["2026-11-18"] = {
        "day": 18,
        "is_trading_day": True,
        "future": False,
        "session_date_us": "2026-11-18",
        "balance_krw": BROKER["equity_krw"],
        "equity_usd": BROKER["equity_usd"],
        "profit_usd": 0.0,
        "profit_krw": 0,
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
        "status": "마감 정산 확정 — 당일 체결 0건(실현 $0), 평가 -$4.90",
        "note": (
            "미국 11-18 세션 마감 정산(NH Open API 실측 2026-11-19 06:01 KST, run 44397133539). "
            "주문체결내역 orr_dt=20261118·20261119 조회 결과 0건, 보유 종목 변동 없음 → 당일 실현손익 $0. "
            f"보유 NVDA 1주(평단 $173.30, 종가 $151.15, -$22.15 / -12.78%)·SGOV 1주($100.59, +$0.06). "
            f"총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) = 외화예수금 ${BROKER['cash_usd']:,.2f}"
            f"({BROKER['cash_krw']:,}원) + 미결제 정산금 ${BROKER['unsettled_usd']:,.2f}({BROKER['unsettled_krw']:,}원)"
            f" + 평가 ${BROKER['eval_usd']:,.2f}. "
            f"전일 대비 총자산 -${abs(day_usd):,.2f}({day_krw:,}원) 전액이 평가손익 변동(NVDA -$4.91·SGOV +$0.01)이며 "
            "11-17 매도대금 $200.23은 미결제 정산금으로 잔류(예수금 아님). "
            f"누적 실현 +${REALIZED_USD:,.2f}(+{REALIZED_KRW:,}원, {TRADES}전 {WINS}승 {LOSSES}패). "
            "SOXS 조건부 집행(NVDA 갭업 ≥+2%)은 개장 시각 예약 실행이 이뤄지지 않아 미집행 — NH 주문 0건으로 확인."
        ),
    }

    ms = cal["monthly_summary"]
    ms.update({
        "total_profit_usd": REALIZED_USD,
        "total_profit_krw": REALIZED_KRW,
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
        "fx_rate_source": "NH Open API 적용환율 tdt_sby_bse_xcg_rt (2026-11-19 06:01 KST 마감 정산)",
        "account_status": (
            f"실전 7일차(11-18 세션) 마감 정산 확정(2026-11-19 06:01 KST): 당일 체결 0건(실현 $0), "
            f"평가 -${abs(BROKER['unreal_usd']):,.2f}. 보유 NVDA 1주($151.15, -$22.15 / -12.78%)·SGOV 1주($100.59, +$0.06). "
            f"총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) · 외화예수금 ${BROKER['cash_usd']:,.2f}"
            f"({BROKER['cash_krw']:,}원, {round(BROKER['cash_usd'] / BROKER['equity_usd'] * 100, 1)}%) + "
            f"미결제 정산금 ${BROKER['unsettled_usd']:,.2f}({BROKER['unsettled_krw']:,}원). "
            f"누적 실현 +${REALIZED_USD:,.2f}({TRADES}전 {WINS}승 {LOSSES}패, 승률 {WIN_RATE}%), "
            f"총자산 손익 {total_usd:+,.2f}달러({total_krw:+,}원)."
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
        p["name"] = "엔비디아" if p["ticker"] == "NVDA" else "아이셰어즈 0-3개월 미국 국채 ETF"
        p["price_as_of"] = "2026-11-18"
        p["broker_mark_price"] = p["market_price"]
        p["quote_last"] = p["market_price"]
        p["unrealized_krw"] = BROKER["positions"][p["ticker"]]["unreal_krw"]
        p["status"] = BROKER["positions"][p["ticker"]]["kind"]
        p["risk_status"] = None
        p["bracket"] = None

    payload = {
        "api_status": "CONNECTED",
        "broker": "NH투자증권 나무 (Open API)",
        "account_no": "202-05-134849",
        "last_synced": now_kst(),
        "session_phase": "POST_MARKET",
        "price_basis": "11-18 정규장 종가 (NH fc_sec_end_pr) — 시간외 세션",
        "price_source": "NH Open API 종가(fc_sec_end_pr) 우선, 없으면 Yahoo 마지막 시세",
        "fx_rate": BROKER["fx_settle"],
        "fx_source": "NH Open API 적용환율(tdt_sby_bse_xcg_rt) 역산 — 2026-11-19 06:01 KST",
        "initial_funding_krw": INITIAL_KRW,
        "initial_funding_usd": INITIAL_USD,
        "current_total_equity_usd": BROKER["equity_usd"],
        "current_total_equity_krw": BROKER["equity_krw"],
        "cash_buffer_usd": BROKER["cash_usd"],
        "cash_buffer_krw": BROKER["cash_krw"],
        "cash_buffer_pct": round(BROKER["cash_usd"] / BROKER["equity_usd"] * 100, 1),
        "cash_buffer_note": ("외화예수금(fc_dca) = 주문가능 현금. 11-17 매도 체결대금 $200.23은 "
                             "미결제 정산금(fc_ny_stl_xcl_amt)으로 예수금에 아직 반영되지 않았다."),
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
            "prev_residual_usd": -9.15,
            "prev_residual_explained": "11-18 06:01 실측 fc_ny_stl_xcl_amt = -9.15 (결제 대기 차감) — 계좌 금액 오류 아님",
        },
        "fills_confirmed": [],
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
        "win_rate_pct": WIN_RATE,
        "trades_summary": f"{TRADES}전 {WINS}승 {LOSSES}패 (승률 {WIN_RATE}%)",
        "positions_market_value_usd": BROKER["eval_usd"],
        "positions": pos_now,
        "recent_trades": [],
        "inverse_hedging_ready": True,
        "inverse_tickers": ["SOXS", "SQQQ"],
        "ledger_consistency": {"checked_at": None},
    }

    for rel in ("data/live_portfolio.json", "docs/live_portfolio.json"):
        p = os.path.join(BASE_DIR, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # 대시보드/피드가 함께 보는 브로커 원본 스냅샷도 남긴다(감사용).
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
        "fx_purchase": BROKER["fx_purchase"],
        "positions": BROKER["positions"],
        "recent_executions_counts": BROKER["executions"],
        "rsp_cd": BROKER["rsp_cd"],
        "rsp_msg": BROKER["rsp_msg"],
    }
    with open(os.path.join(BASE_DIR, "data", "nh_account_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)


def main() -> None:
    assert_identity()
    calc = update_calendar()
    update_live_portfolio(calc)
    print(f"✅ 11-18 정산 반영 | 총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) "
          f"| 현금 ${BROKER['cash_usd']:,.2f}+미결제 ${BROKER['unsettled_usd']:,.2f} "
          f"| 평가 ${BROKER['eval_usd']:,.2f}({BROKER['unreal_usd']:+,.2f}) "
          f"| 당일 {calc['day_usd']:+,.2f}달러({calc['day_krw']:+,}원) "
          f"| 누적 실현 +${REALIZED_USD:,.2f} / 순 {calc['net_usd']:+,.2f}")


if __name__ == "__main__":
    main()
