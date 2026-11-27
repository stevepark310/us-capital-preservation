#!/usr/bin/env python3
"""11-27(금) 미국 정규장(블랙프라이데이 반일장) 마감 정산 — NH Open API 실측.

입력은 추정치가 아니라 GitHub Actions `nh_trade_dispatch.yml` `mode=test_balance`
(run 46082643734, 2026-11-28 08:28 KST)가 돌려준 NH Open API 원본 응답이다. 이 스크립트는
  * data/daily_trading_calendar.json 의 2026-11-27 레코드와 monthly_summary
  * data/live_portfolio.json · docs/live_portfolio.json
  * data/nh_account_snapshot.json (감사용 원본 스냅샷)
를 실측값으로만 갱신한다. 화면(index.html·cube_report·위젯)은 이어서
`scripts/sync_live_portfolio.py` 와 `scripts/build_full_cube.py` 가 원장에서 다시 만든다.

11-26(목)은 추수감사절 휴장, 11-27(금)은 13:00 ET 반일장이다(core/market_clock.US_HALF_DAYS_2026).

브로커 총자산 항등식(이번 정산에서 확인):
    fc_aet_amt = fc_dca + fc_ny_stl_xcl_amt + fc_eal_amt
    881.27     = 780.56 + 0.00              + 100.71      (잔차 0.00)

당일 체결(11-27 ET 세션):
  - 체결 없음(0건). /gbstock/inquiry/v1/unexecuted 를 orr_dt 20261128·20261127·20261126
    3회 조회했고 모두 `rsp_cd=13578 조회할 내역이 없습니다`.

대조 항등식(총자산 변동 분해, 잔차 0.00):
    +0.03 = 실현 0.00 + 평가변동 (0.18 - 0.15 = +0.03) - 수수료 0.00
    원화 총자산은 -1,017원: 달러 총자산 +0.03인데 적용환율이 1343.9 → 1342.7로 내려
    원화 평가액이 줄었다(환율 효과이며 손실 체결이 아니다).

자료 출처(run 46082643734 원문):
    fc_aet_amt 881.27 / fc_dca 780.56 / krw_dca 1048057 / fc_eal_amt 100.71 /
    eal_amt_sum 135223 / fc_abk_amt 100.53 / abk_amt 135042 / fc_eal_pls_amt 0.18 /
    eal_pls_sum_amt 181 / tot_aet_amt 1183280 / tdt_sby_bse_xcg_rt 1342.7 /
    pft_rt 0.17 / krw_pft_rt 0.13 / SGOV cns_bse_bnc_qty 1 · sll_pbl_qty1 1
"""
import json
import os
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- NH Open API 실측 (2026-11-28 08:28 KST, workflow run 46082643734) ----
BROKER = {
    "fetched_at_kst": "2026-11-28 08:28 KST",
    "workflow_run_id": 46082643734,
    "rsp_cd": "00166",
    "rsp_msg": "조회가 완료되었습니다.",
    "equity_usd": 881.27,          # fc_aet_amt
    "equity_krw": 1183280,         # tot_aet_amt
    "cash_usd": 780.56,            # fc_dca (외화예수금 = 주문가능 현금)
    "cash_krw": 1048057,           # krw_dca
    "unsettled_usd": 0.0,          # fc_ny_stl_xcl_amt
    "unsettled_krw": 0,            # krw_ny_stl_xcl_amt
    "eval_usd": 100.71,            # fc_eal_amt
    "eval_krw": 135223,            # eal_amt_sum
    "cost_usd": 100.53,            # fc_abk_amt
    "cost_krw": 135042,            # abk_amt
    "unreal_usd": 0.18,            # fc_eal_pls_amt
    "unreal_krw": 181,             # eal_pls_sum_amt
    "return_pct": 0.17,            # pft_rt (달러)
    "return_pct_krw": 0.13,        # krw_pft_rt
    "fx_settle": 1342.7,           # tdt_sby_bse_xcg_rt (결제 기준환율)
    "fx_purchase": 1343.300507,    # phs_xcg_rt
    "positions": {
        "SGOV": {"qty": 1, "avg": 100.53, "mark": 100.71, "eval": 100.71,
                 "unreal": 0.18, "unreal_krw": 181, "ret": 0.17, "fx_purchase": 1343.30,
                 "sellable_qty": 1,
                 "kind": "1주 보유 — 캐리 파킹 (11-27 반일장 종가 $100.71)"},
    },
    "executions": {                # /gbstock/inquiry/v1/unexecuted (orr_dt별)
        "20261128": 0,
        "20261127": 0,
        "20261126": 0,
    },
}

FILLS = []

# ---- 누적 실현 손익 (변동 없음) ----
REALIZED_DAY_USD = 0.00
REALIZED_DAY_KRW = 0
REALIZED_USD = -17.43
REALIZED_KRW = round(REALIZED_USD * BROKER["fx_settle"])   # 원장 관례: 당일 적용환율로 환산
TRADES, WINS, LOSSES, WIN_RATE = 11, 6, 5, round(6 / 11 * 100, 1)

# ---- 전일 확정값 (11-26 09:48 KST 실측 — 11-25 세션). 11-26은 추수감사절 휴장 ----
PREV_EQUITY_USD, PREV_EQUITY_KRW = 881.24, 1184297
PREV_UNREAL_USD_11_25 = 0.15   # 11-25 세션 마감 평가손익
PREV_FX = 1343.9

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
    cost = round(sum(p["avg"] * p["qty"] for p in BROKER["positions"].values()), 2)
    assert cost == BROKER["cost_usd"], f"매입금액 합계 불일치: {cost}"
    delta = round(REALIZED_DAY_USD + (BROKER["unreal_usd"] - PREV_UNREAL_USD_11_25) - FEE_TOTAL_USD, 2)
    day = round(BROKER["equity_usd"] - PREV_EQUITY_USD, 2)
    assert delta == day, f"총자산 변동 분해 불일치: {delta} != {day}"
    assert BROKER["unsettled_usd"] == 0.0
    assert sum(BROKER["executions"].values()) == len(FILLS) == 0


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

    recs = cal.setdefault("daily_records", {})
    # 11-26 추수감사절 휴장 — future 플래그만 내리고 휴장 사유를 남긴다(금액 없음).
    recs["2026-11-26"] = {"day": 26, "is_trading_day": False, "future": False,
                          "note": "추수감사절(Thanksgiving) 휴장 — 미국장 미개장"}
    recs["2026-11-27"] = {
        "day": 27,
        "is_trading_day": True,
        "future": False,
        "is_half_day": True,
        "session_date_us": "2026-11-27",
        "session_note": "블랙프라이데이 반일장 (13:00 ET 마감)",
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
        "fx_rate": BROKER["fx_settle"],
        "status": "마감 정산 확정 — 반일장 체결 없음(0건), 보유 SGOV 1주($100.71) 유지",
        "settlement_events": [],
        "note": (
            "미국 11-27(금) 블랙프라이데이 반일장(13:00 ET) 마감 정산(NH Open API 실측 "
            "2026-11-28 08:28 KST, run 46082643734, rsp_cd=00166). 당일 체결 0건 — "
            "orr_dt 20261128·20261127·20261126 미체결 조회 3회 모두 '조회할 내역이 없습니다'(13578). "
            f"총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) = 외화예수금 "
            f"${BROKER['cash_usd']:,.2f}({BROKER['cash_krw']:,}원, "
            f"{round(BROKER['cash_usd'] / BROKER['equity_usd'] * 100, 1)}%) + 미결제 0.00 + "
            f"평가 ${BROKER['eval_usd']:,.2f}; 항등식 잔차 $0.00. "
            f"보유 SGOV 1주(평단 $100.53, 종가 $100.71, 평가 +${BROKER['unreal_usd']:.2f}, "
            f"+{BROKER['return_pct']}%) 유지 — 매도가능 1주. "
            f"당일 총자산 +${day_usd:.2f} / {day_krw:+,}원(적용환율 {PREV_FX}→{BROKER['fx_settle']} "
            f"하락으로 원화 환산은 감소). 누적 실현 -${abs(REALIZED_USD):,.2f}({REALIZED_KRW:,}원, "
            f"{TRADES}전 {WINS}승 {LOSSES}패, 승률 {WIN_RATE}%), 평가 +${BROKER['unreal_usd']:.2f}, "
            f"총자산 손익 {total_usd:+,.2f}달러({total_krw:+,}원)."
        ),
    }
    # 11-28(토) 주말 — 휴장 확정.
    recs["2026-11-28"] = {"day": 28, "is_trading_day": False, "future": False,
                          "note": "주말 휴장"}

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
        "fx_rate_source": "NH Open API 적용환율 tdt_sby_bse_xcg_rt (2026-11-28 08:28 KST 마감 정산)",
        "account_status": (
            f"실전 13일차(11-27 금 반일장) 마감 정산 확정(2026-11-28 08:28 KST): 체결 0건. "
            f"보유 SGOV 1주($100.71, 평가 +${BROKER['unreal_usd']:.2f}) 유지 — 매도가능 1주. "
            f"총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) · 외화예수금 "
            f"${BROKER['cash_usd']:,.2f}({BROKER['cash_krw']:,}원, "
            f"{round(BROKER['cash_usd'] / BROKER['equity_usd'] * 100, 1)}%) · 미결제 0.00. "
            f"누적 실현 -${abs(REALIZED_USD):,.2f}({TRADES}전 {WINS}승 {LOSSES}패, 승률 {WIN_RATE}%), "
            f"평가 +${BROKER['unreal_usd']:.2f}, 총자산 손익 {total_usd:+,.2f}달러({total_krw:+,}원)."
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
        p["price_as_of"] = "2026-11-27"
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
        "price_basis": "11-27 블랙프라이데이 반일장 종가 (NH fc_sec_end_pr)",
        "price_source": "NH Open API 종가(fc_sec_end_pr) 우선, 없으면 Yahoo 마지막 시세",
        "fx_rate": BROKER["fx_settle"],
        "fx_source": "NH Open API 적용환율(tdt_sby_bse_xcg_rt) — 2026-11-28 08:28 KST 마감 정산",
        "initial_funding_krw": INITIAL_KRW,
        "initial_funding_usd": INITIAL_USD,
        "current_total_equity_usd": BROKER["equity_usd"],
        "current_total_equity_krw": BROKER["equity_krw"],
        "cash_buffer_usd": BROKER["cash_usd"],
        "cash_buffer_krw": BROKER["cash_krw"],
        "cash_buffer_pct": round(BROKER["cash_usd"] / BROKER["equity_usd"] * 100, 1),
        "cash_buffer_note": ("외화예수금(fc_dca) = 주문가능 현금. 미결제 정산금 0.00. "
                             "11-23 SQQQ 매도 순액 $52.86은 11-25 T+2 결제 완료."),
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
            "prev_residual_explained": "전일(11-26 09:48)에도 잔차 0.00",
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
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(p + ".tmp", p)

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
        "settlement_events": [],
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
    print(f"✅ 11-27 정산 반영 | 총자산 ${BROKER['equity_usd']:,.2f}({BROKER['equity_krw']:,}원) "
          f"| 현금 ${BROKER['cash_usd']:,.2f}+미결제 ${BROKER['unsettled_usd']:,.2f} "
          f"| 평가 ${BROKER['eval_usd']:,.2f}({BROKER['unreal_usd']:+,.2f}) "
          f"| 당일 {calc['day_usd']:+,.2f}달러({calc['day_krw']:+,}원) "
          f"| 누적 실현 ${REALIZED_USD:,.2f} / 순 {calc['net_usd']:+,.2f}")


if __name__ == "__main__":
    main()
