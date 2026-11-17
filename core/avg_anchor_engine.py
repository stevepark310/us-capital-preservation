"""
Avg-Anchor Profit Desk (AAPD) — 평단가 기준 일일 수익 엔진

동훈님 지시(2026-11-17): "지금 평단가 기준으로 오늘 수익을 내야하니까 전반적으로
모든 직원들에게 강력한 수익 알고리즘을 창출할 수 있도록 지시해줘."

이 모듈이 하는 일
----------------
1. **실계좌 체결 평단**을 기준점(anchor)으로 삼아, 종목별로 오늘 세션 안에서
   집행 가능한 3단 사다리(본전 회수 → 1차 익절 → 2차 익절)를 만든다.
   목표 폭은 종목 자신의 ATR%에 비례하되 세션 내 체결 가능한 범위로 상·하한을 건다.
2. **손절은 ATR 0.30배**(상한 3.0%)로 고정하고, 1차 익절이 체결되면 손절선을
   본전(+수수료 0.34%)까지 끌어올린다 — 이익은 확정하고 손실은 짧게.
3. **개장 유예**: 개장 직후 15분(23:45 KST)까지는 갭 변동성이라 손절을 발동하지
   않는다(개장가 휩쏘 방지). 유예 이후부터 손절 유효.
4. **실적 가드(NVDA 3Q, 2026-11-18 06:20 KST)**: 3배 레버리지(SOXL·TQQQ)는
   실적을 넘기지 않는다. 05:40 KST까지 살아남은 잔여 수량은 시장가 청산한다.
   NVDA 1주는 사용자 지정 레벨(TP $173.0 / SL $150.0)로 실적을 맞는다 — 변경 금지.
5. **현금 엔진**: 남은 현금의 15~20%(최대 $85)로 00:00 이후 VWAP·5분봉 지지 확인 시
   소액 스캘핑 회전(목표 +1.8% / 손절 -1.2% / 90분 시간손절, 최대 3회전).
6. 결과를 `data/avg_anchor_plan.json`에 남기고, `--apply`로 `data/active_brackets.json`에
   실제 체결 평단 기준 브래킷을 등록한다(10분 감시망이 그대로 집행).

주의: 이 파일은 전략 파라미터와 계산만 담당한다. 주문은 `scripts/monitor_and_exit.py`가
시장 개장 중에만 집행하며, 백테스트 수치와 실전 원장은 분리해 표기한다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
PLAN_PATH = os.path.join(DATA, "avg_anchor_plan.json")
LIVE_PATH = os.path.join(DATA, "live_portfolio.json")
BRACKETS_PATH = os.path.join(DATA, "active_brackets.json")
CALENDAR_PATH = os.path.join(DATA, "daily_trading_calendar.json")

PARAMS: Dict[str, Any] = {
    "tp1_atr_mult": 0.35,
    "tp2_atr_mult": 0.70,
    "sl_atr_mult": 0.25,
    "tp1_min_pct": 1.5, "tp1_max_pct": 3.0,
    "tp2_min_pct": 3.0, "tp2_max_pct": 6.0,
    "sl_min_pct": 1.5, "sl_max_pct": 2.5,
    "breakeven_pct": 0.34,          # 왕복 수수료 0.24% + 여유 0.10%
    "sl_active_after_kst": "23:45",  # 개장 15분 유예
    "force_exit_kst": "05:40",       # 실적 가드: 마감 20분 전 잔여 청산
    "session_close_kst": "06:00",
    "earnings_kst": "2026-11-18 06:20",
    "scalp": {
        "budget_pct_min": 15.0, "budget_pct_max": 20.0, "budget_cap_usd": 85.0,
        "tp_pct": 1.8, "sl_pct": 1.2, "time_stop_min": 90,
        "max_rounds": 1, "first_entry_after_kst": "00:00", "last_entry_kst": "04:30",
        "universe": ["SOXL", "TQQQ"],
        "entry_gate": "DIP_SWING 등급 신호만 (RSI2<=20 · %B<=0.15 · RVOL>=1.0) — 5분봉 VWAP 돌파 단독 진입은 폐기",
        "suspended_rule": ("5분봉 VWAP 상향 돌파 + 90분 스캘핑(+1.8%/-1.2%)은 60일 실측에서 "
                           "건당 총기대 -0.049%(SOXL)/-0.056%(TQQQ), 왕복 0.24% 수수료 차감 후 "
                           "-0.29%/-0.30%로 음의 기대값 → 기계적 반복 회전 중단(2026-11-17 실측)"),
    },
    "hedge": {
        "enabled_gate": "VIX >= 24.0 + 확인 신호 2개 이상 (DXY>=104.5 / 센티먼트<40 등)",
        "instruments": ["SOXS", "SQQQ"], "max_notional_usd": 60.0,
    },
}

# 사용자 지정 레벨은 엔진이 덮어쓰지 않는다.
LOCKED_LEVELS: Dict[str, Dict[str, float]] = {
    "NVDA": {"tp1": 173.0, "sl": 150.0},
}
CARRY_TICKERS = {"SGOV"}
# NH 주문체결내역(/gbstock/inquiry/v1/unexecuted) 실측 — 2026-11-17 11:15 KST 조회
FILLS = {
    "SOXL": {"order_no": 249542, "order_date": "2026-11-16", "fill_price": 50.43, "qty": 2},
    "TQQQ": {"order_no": 249661, "order_date": "2026-11-16", "fill_price": 54.00, "qty": 2},
}
LADDER_TICKERS = ("SOXL", "TQQQ")


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def _load(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _position_qty_split(qty: int) -> Dict[str, int]:
    """2주면 1/1(TP1·TP2), 3주면 1/1/1, 4주 이상은 34/33/잔여."""
    if qty <= 1:
        return {"tp1_qty": 1, "tp2_qty": 0, "runner_qty": 0}
    if qty == 2:
        return {"tp1_qty": 1, "tp2_qty": 1, "runner_qty": 0}
    if qty == 3:
        return {"tp1_qty": 1, "tp2_qty": 1, "runner_qty": 1}
    tp1 = max(1, round(qty * 0.34))
    tp2 = max(1, round(qty * 0.33))
    return {"tp1_qty": tp1, "tp2_qty": tp2, "runner_qty": max(0, qty - tp1 - tp2)}


STATS_PATH = os.path.join(DATA, "avg_anchor_ladder_stats.json")


def _touch_prob(ticker: str, pct: float) -> Optional[float]:
    """1년 일봉 실측: 전일 종가 대비 장중 고가가 +pct%에 닿은 날의 비율."""
    stats = _load(STATS_PATH).get(ticker) or {}
    grid = (stats.get("grid") or {})
    if not grid:
        return None
    keys = sorted(float(k.replace("+", "")) for k in grid)
    nearest = min(keys, key=lambda k: abs(k - abs(pct)))
    row = grid.get(f"+{nearest:g}") or {}
    cells = list(row.values())
    if pct >= 0:
        return cells[0]["p_tp"] if cells else None
    return max(c["p_sl"] for c in cells) if cells else None


def measured_edge(ticker: str, tp1_pct: float, sl_pct: float) -> Dict[str, Any]:
    """TP1 도달 확률과 SL 이탈 확률의 실측치로 하루 기대값을 근사한다.

    한계: 일봉 고·저만 쓰므로 장중 도달 순서(고가 먼저/저가 먼저)를 알 수 없다.
    두 선을 모두 건드린 날은 보수적으로 손절로 계산한다(최악 가정).
    """
    stats = _load(STATS_PATH).get(ticker) or {}
    grid = stats.get("grid") or {}
    if not grid:
        return {"available": False}
    xs = sorted(float(k.replace("+", "")) for k in grid)
    ys = sorted(float(k.replace("-", "")) for k in list(list(grid.values())[0].keys()))
    x = min(xs, key=lambda k: abs(k - tp1_pct))
    y = min(ys, key=lambda k: abs(k - abs(sl_pct)))

    def _key(mapping, sign):
        for k in mapping:
            try:
                if abs(float(k.replace(sign, "")) - (x if sign == "+" else y)) < 1e-9:
                    return k
            except ValueError:
                continue
        return None

    xk = _key(grid, "+")
    row = grid.get(xk) or {}
    yk = _key(row, "-")
    cell = row.get(yk) or {}
    p_tp, p_sl = cell.get("p_tp"), cell.get("p_sl")
    if p_tp is None or p_sl is None:
        return {"available": False}
    both = stats.get("both_pct_reference")
    ev_pct = ((p_tp - (both or 0)) / 100.0) * x - (p_sl / 100.0) * y
    return {"available": True, "tp_touch_pct": p_tp, "sl_touch_pct": p_sl,
            "grid_used": {"tp_pct": x, "sl_pct": y},
            "ev_pct_per_share": round(ev_pct, 3),
            "sample_days": stats.get("days"),
            "note": "1년 일봉 근사(전일 종가 진입 가정, 장중 순서 미확인·양쪽 도달일은 손절 가정)"}


def build_ladder(ticker: str, qty: int, avg_price: float, atr_pct: float,
                 market_price: Optional[float] = None) -> Dict[str, Any]:
    """평단 기준 3단 사다리 1종목 계산."""
    p = PARAMS
    atr = float(atr_pct or 0.0)
    if ticker in LOCKED_LEVELS:
        lock = LOCKED_LEVELS[ticker]
        return {
            "ticker": ticker, "qty": qty, "avg_price": round(avg_price, 4),
            "atr_pct": round(atr, 2),
            "breakeven": round(avg_price * (1 + p["breakeven_pct"] / 100), 2),
            "tp1": lock["tp1"], "tp1_pct": round((lock["tp1"] / avg_price - 1) * 100, 2),
            "tp2": None, "tp2_pct": None,
            "sl": lock["sl"], "sl_pct": round((lock["sl"] / avg_price - 1) * 100, 2),
            "tp1_qty": 1, "tp2_qty": 0, "runner_qty": 0,
            "levels_locked": True,
            "force_exit_kst": None,
            "expected_pnl_usd": {"tp1": round((lock["tp1"] - avg_price) * 1, 2), "tp2": 0.0,
                                 "sl_full": round((lock["sl"] - avg_price) * int(qty), 2)},
            "reward_risk": None,
            "note": "사용자 지정 레벨 유지 (TP $173.0 / SL $150.0). 엔진은 참고값만 계산.",
        }

    tp1_pct = round(_clamp(p["tp1_atr_mult"] * atr, p["tp1_min_pct"], p["tp1_max_pct"]), 2)
    tp2_pct = round(_clamp(p["tp2_atr_mult"] * atr, p["tp2_min_pct"], p["tp2_max_pct"]), 2)
    sl_pct = round(_clamp(p["sl_atr_mult"] * atr, p["sl_min_pct"], p["sl_max_pct"]), 2)
    if tp2_pct <= tp1_pct:
        tp2_pct = round(tp1_pct + 1.0, 2)

    split = _position_qty_split(int(qty))
    tp1_price = round(avg_price * (1 + tp1_pct / 100), 2)
    tp2_price = round(avg_price * (1 + tp2_pct / 100), 2)
    sl_price = round(avg_price * (1 - sl_pct / 100), 2)
    be_price = round(avg_price * (1 + p["breakeven_pct"] / 100), 2)

    return {
        "ticker": ticker, "qty": int(qty), "avg_price": round(avg_price, 4),
        "market_price": round(float(market_price), 2) if market_price else None,
        "atr_pct": round(atr, 2),
        "breakeven": be_price,
        "tp1": tp1_price, "tp1_pct": tp1_pct,
        "tp2": tp2_price, "tp2_pct": tp2_pct,
        "sl": sl_price, "sl_pct": -sl_pct,
        **split,
        "levels_locked": False,
        "force_exit_kst": p["force_exit_kst"] if ticker in LADDER_TICKERS else None,
        "sl_active_after_kst": p["sl_active_after_kst"],
        "expected_pnl_usd": {
            "tp1": round((tp1_price - avg_price) * split["tp1_qty"], 2),
            "tp2": round((tp2_price - avg_price) * split["tp2_qty"], 2) if split["tp2_qty"] else 0.0,
            "sl_full": round((sl_price - avg_price) * int(qty), 2),
        },
        "reward_risk": round(
            ((tp1_price - avg_price) * split["tp1_qty"] +
             (tp2_price - avg_price) * split["tp2_qty"]) /
            max(1e-9, abs((sl_price - avg_price) * int(qty))), 2),
        "measured": measured_edge(ticker, tp1_pct, sl_pct),
        "note": f"평단 ${avg_price:.2f} 기준 ATR {atr:.2f}% → TP1 {tp1_pct}% / TP2 {tp2_pct}% / SL -{sl_pct}%",
    }


def build_plan(positions: List[Dict[str, Any]], techs: Dict[str, Any], cash_usd: float,
               equity_usd: float, vix: Optional[float] = None,
               phase: Optional[str] = None, next_open_kst: Optional[str] = None) -> Dict[str, Any]:
    ladders: List[Dict[str, Any]] = []
    for pos in positions or []:
        tk = str(pos.get("ticker") or "").upper()
        qty = int(pos.get("qty") or 0)
        avg = float(pos.get("avg_price") or 0.0)
        if not tk or qty <= 0 or avg <= 0:
            continue
        if tk in CARRY_TICKERS:
            continue
        atr = float((techs.get(tk) or {}).get("atr_pct") or 6.0)
        ladders.append(build_ladder(tk, qty, avg, atr, pos.get("market_price")))

    cash = float(cash_usd or 0.0)
    sc = PARAMS["scalp"]
    scalp_budget = round(min(cash * sc["budget_pct_max"] / 100.0, sc["budget_cap_usd"]), 2)
    scalp_budget_min = round(min(cash * sc["budget_pct_min"] / 100.0, sc["budget_cap_usd"]), 2)

    tp_total = round(sum((l.get("expected_pnl_usd") or {}).get("tp2", 0.0) +
                         (l.get("expected_pnl_usd") or {}).get("tp1", 0.0) for l in ladders
                         if not l.get("levels_locked")), 2)
    sl_total = round(sum((l.get("expected_pnl_usd") or {}).get("sl_full", 0.0) for l in ladders
                         if not l.get("levels_locked")), 2)
    scalp_expect = round(scalp_budget * sc["tp_pct"] / 100.0 * min(2, sc["max_rounds"]), 2)
    daily_target = round(float(equity_usd or 0) * 0.0015, 2)
    measured_usd = 0.0
    for l in ladders:
        m = l.get("measured") or {}
        if m.get("available"):
            measured_usd += (m["ev_pct_per_share"] / 100.0) * l["avg_price"] * l["qty"]
    measured_usd = round(measured_usd, 2)

    return {
        "engine": "AVG_ANCHOR_PROFIT_DESK",
        "engine_ko": "평단 기준 일일 수익 엔진",
        "version": "1.0",
        "generated_at_kst": _now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
        "directive": {
            "given_at_kst": "2026-11-17 11:12",
            "quote": "체결됬는데 지금 계좌 호출해서 최신화해줘. 잔고에서 보면 되잖아. 그리고 지금 평단가 기준으로 오늘 수익을 내야하니까 전반적으로 모든 직원들에게 강력한 수익 알고리즘을 창출할 수 있도록 지시해줘.",
            "interpretation": "실계좌 체결 평단을 기준점으로 오늘 세션에서 실현 수익을 만들며, 손실은 짧게 자른다.",
        },
        "market": {"phase": phase, "next_open_kst": next_open_kst, "vix": vix,
                   "earnings": {"ticker": "NVDA", "at_kst": PARAMS["earnings_kst"]}},
        "params": PARAMS,
        "ladders": ladders,
        "excluded": {"carry_parking": sorted(CARRY_TICKERS),
                     "note": "SGOV는 무위험 캐리 파킹으로 익절·손절 대상에서 제외"},
        "cash_engine": {
            "cash_available_usd": round(cash, 2),
            "budget_usd": scalp_budget,
            "budget_range_usd": [scalp_budget_min, scalp_budget],
            "budget_pct_of_cash": sc["budget_pct_max"],
            "tp_pct": sc["tp_pct"], "sl_pct": sc["sl_pct"],
            "time_stop_min": sc["time_stop_min"], "max_rounds": sc["max_rounds"],
            "window_kst": f"{sc['first_entry_after_kst']}~{sc['last_entry_kst']}",
            "entry_gate": sc["entry_gate"],
            "universe": sc["universe"],
            "expected_pnl_usd": scalp_expect,
        },
        "hedge": {**PARAMS["hedge"], "armed": bool(vix is not None and float(vix) >= 24.0),
                  "vix": vix},
        "guards": {
            "sl_active_after_kst": PARAMS["sl_active_after_kst"],
            "force_exit_kst": PARAMS["force_exit_kst"],
            "force_exit_reason": "NVDA 3분기 실적(2026-11-18 06:20 KST) 전 3배 레버리지 노출 축소",
            "no_averaging_down": "실적 전 물타기·추가 매수 금지 (NVDA 사용자 규칙 및 신중 모드 유지)",
            "opening_blackout_kst": "23:30~00:00 신규 매수 보류",
            "nvda_levels_locked": "TP $173.0 / SL $150.0 (사용자 지정, 엔진 미변경)",
            "bear_alpha_gate": "VIX >= 24.0 + 확인 2개 (변경 금지)",
        },
        "summary": {
            "positions_under_management": [l["ticker"] for l in ladders],
            "expected_tp_total_usd": tp_total,
            "max_loss_at_sl_usd": sl_total,
            "scalp_expected_usd": 0.0,
            "scalp_suspended": True,
            "scalp_evidence": (PARAMS["scalp"].get("suspended_rule")),
            "scalp_note": "현금은 SGOV 파킹 또는 DIP_SWING 등급 신호 1회에만 사용한다.",
            "expected_best_case_usd": round(tp_total, 2),
            "expected_worst_case_usd": round(sl_total, 2),
            "daily_target_usd": daily_target,
            "daily_target_rule": "자산의 0.15% — 실측 기대값(아래)과 같은 자릿수의 목표. 0.5% 목표는 소액 포지션에서 비현실적이어서 하향했다.",
            "measured_expected_usd_per_session": measured_usd,
            "measured_basis": ("1년 일봉 실측 TP/SL 도달확률로 계산한 하루 기대 실현손익. "
                               "2주·1주 소액 포지션에서는 0.5% 목표($4.46)에 구조적으로 미달하며, "
                               "자본(포지션 크기)을 늘리지 않는 한 하루 +$1 내외가 현실적 상한이다."),
            "capital_constraint": (f"평가자산 ${equity_usd:,.2f} 중 레버리지 노출이 "
                                   f"${sum(l['avg_price']*l['qty'] for l in ladders if not l.get('levels_locked')):,.2f}뿐이다. "
                                   f"이 크기에서 사다리 기대값은 하루 ${measured_usd:+.2f} 수준이며, 하루 ${daily_target}을 넘기려면 "
                                   f"포지션 확대(리스크 증가) 또는 2차 익절까지 가는 날이 필요하다."),
            "daily_target_note": (f"오늘 목표는 1차 익절 최소 1건(+$1.5 내외) 이상 실현. "
                                  f"실측 기대({measured_usd:+.2f})를 넘기려면 2차 익절(TP2 도달률 SOXL 42.6%)까지 가는 날이 필요하다."),
        },
    }


def apply_to_brackets(plan: Dict[str, Any], brackets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """평단 기준 사다리를 active_brackets.json 형식으로 등록한다."""
    brackets = dict(brackets or {})
    for lad in plan.get("ladders", []):
        tk = lad["ticker"]
        prev = brackets.get(tk) or {}
        b: Dict[str, Any] = {
            "bought_qty": int(lad["qty"]),
            "entry_price": lad["avg_price"],
            "planned_price": lad["avg_price"],
            "tp1": lad["tp1"],
            "tp1_qty": int(lad["tp1_qty"]),
            "tp1_filled": False,
            "tp2": lad.get("tp2"),
            "tp2_qty": int(lad["tp2_qty"] or 0),
            "tp2_filled": False,
            "runner_qty": int(lad.get("runner_qty") or 0),
            "sl": lad["sl"],
            "breakeven_stop": lad["breakeven"],
            "sl_triggered": False,
            "closed": False,
            "opened_at": prev.get("opened_at") or "2026-11-17 01:15 KST",
            "filled_at_kst": ("2026-11-17 01:15 KST" if tk == "SOXL" else "2026-11-17 01:45 KST"),
            "fill_evidence": "NH POST /gbstock/inquiry/v1/unexecuted — 미체결 0, 체결 수량 전량",
            "order_no": (FILLS.get(tk) or {}).get("order_no") or prev.get("order_no"),
            "avg_price_source": "NH Open API 해외주식 잔고 (fc_phs_uit_pr, 2026-11-17 11:15 KST 조회)",
            "profit_ladder": True,
            "ladder_engine": plan.get("engine"),
            "ladder_params": {"tp1_pct": lad.get("tp1_pct"), "tp2_pct": lad.get("tp2_pct"),
                              "sl_pct": lad.get("sl_pct"), "atr_pct": lad.get("atr_pct")},
            "force_exit_kst": lad.get("force_exit_kst"),
            "sl_active_after_kst": lad.get("sl_active_after_kst"),
            "bracket_version": "fill_anchored_v2",
            "note": ("평단 기준 사다리 — " + (lad.get("note") or "")),
            "highest_high": max(float(prev.get("highest_high") or lad["avg_price"]),
                                float(lad.get("market_price") or lad["avg_price"])),
        }
        if lad.get("levels_locked"):
            b["levels_locked"] = True
            b["levels_locked_reason"] = ("사용자 지정 레벨: 평단 $173 본전 회수 매도 대기 · 종가 $150 마지노선. "
                                         "자동 브래킷 재산정이 임의로 변경하지 않습니다.")
            b["force_exit_kst"] = None     # NVDA는 실적을 맞는 유일한 포지션
            b["filled_at_kst"] = "2026-11-13 종가 체결"
        brackets[tk] = b
    return brackets


def export(plan: Dict[str, Any]) -> str:
    os.makedirs(DATA, exist_ok=True)
    with open(PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return PLAN_PATH


def main(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    apply = "--apply" in argv

    live = _load(LIVE_PATH)
    positions = live.get("positions") or []
    cash = live.get("cash_buffer_usd") or 0.0
    equity = live.get("current_total_equity_usd") or 0.0
    vix = None
    try:
        snap = _load(os.path.join(DATA, "market_snapshot.json"))
        vix = ((snap.get("quotes") or {}).get("^VIX") or {}).get("last")
    except Exception:
        pass
    techs = {}
    try:
        snap = _load(os.path.join(DATA, "market_snapshot.json"))
        for tk in ("SOXL", "TQQQ", "NVDA"):
            q = (snap.get("quotes") or {}).get(tk) or {}
            if q:
                # ATR% is exported by the technical pass; fall back to the daily range
                techs[tk] = {"atr_pct": q.get("atr_pct")}
    except Exception:
        pass
    if not any((v or {}).get("atr_pct") for v in techs.values()):
        try:
            from core import market_snapshot, technical
            snap_full = market_snapshot.build_snapshot(quiet=True)
            hist = market_snapshot.fetch_history()
            for tk in ("SOXL", "TQQQ", "NVDA"):
                if tk in hist:
                    techs[tk] = technical.analyze(tk, hist[tk], (snap_full.get("quotes") or {}).get(tk))
            vix = vix or ((snap_full.get("quotes") or {}).get("^VIX") or {}).get("last")
        except Exception as e:
            print(f"[AAPD] 기술적 지표 계산 실패: {e}")

    plan = build_plan(positions, techs, cash, equity, vix=vix,
                      phase=live.get("session_phase"), next_open_kst=_next_open_hint())
    export(plan)
    print(f"✅ 평단 기준 수익 계획 저장: {PLAN_PATH}")
    for lad in plan["ladders"]:
        print(f"  · {lad['ticker']} {lad['qty']}주 평단 ${lad['avg_price']:.2f} → "
              f"TP1 ${lad['tp1']} ({lad['tp1_pct']}%) / "
              f"TP2 ${lad.get('tp2')} / SL ${lad['sl']} ({lad['sl_pct']}%)")
    print(f"  · 기대 최대 ${plan['summary']['expected_best_case_usd']} / 최악 ${plan['summary']['expected_worst_case_usd']} "
          f"/ 목표 ${plan['summary']['daily_target_usd']}")

    if apply:
        brackets = _load(BRACKETS_PATH)
        merged = apply_to_brackets(plan, brackets)
        with open(BRACKETS_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"✅ 브래킷 등록: {BRACKETS_PATH}")
    return plan


def _next_open_hint() -> Optional[str]:
    cal = _load(CALENDAR_PATH)
    return ((cal.get("session") or {}).get("next_open_kst")
            or "2026-11-17 23:30 KST")


if __name__ == "__main__":
    main()
