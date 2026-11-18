"""1회 진입 예산·사이징 실측 도구 (읽기 전용 · 주문 없음).

용도
----
`core/execution_policy.entry_budget()`과 `core/autotrader._size()`가 현재 계좌
(`data/live_portfolio.json`)와 유니버스 4종목(SOXL·TQQQ·SOXS·SQQQ)에서 실제로
어떤 예산·수량·리스크를 내는지 **엔진 코드를 그대로 실행해** 확인한다.

- 예산: VIX 구간별로 구성요소(cash_pct·equity_pct·abs_cap·cash_after_reserve)와
  VIX/변동성 계수 적용 후 실효 금액.
- 사이징: 신호를 주입해 4종목 각각의 `qty`·명목·손절%·1회 리스크를 산출.
  qty가 0이면 "매수 교착"으로 표시한다.

주문은 절대 나가지 않는다(라우터 대역은 DRY_RUN만 반환, 상태 파일 저장 없음).

    python3 scripts/research/measure_entry_budget.py
    python3 scripts/research/measure_entry_budget.py --json data/entry_budget_probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

import pandas as pd  # noqa: E402

from core import execution_policy  # noqa: E402
from core import strategy_evolver  # noqa: E402
from core.autotrader import AutonomousTrader, HEDGE_NOTIONAL_CAP  # noqa: E402

LIVE = os.path.join(BASE_DIR, "data", "live_portfolio.json")
SNAPSHOT = os.path.join(BASE_DIR, "data", "market_snapshot.json")
DAILY_CACHE = os.path.join(BASE_DIR, "data_cache", "backtest_daily.pkl")
UNIVERSE = ["SOXL", "TQQQ", "SOXS", "SQQQ"]
VIX_GRID = [17.5, 22.0, 25.46, 29.0]


class DryRouter:
    """주문 대역 — 어떤 경우에도 DRY_RUN만 돌려준다."""

    def __init__(self) -> None:
        self.dry_run = True
        self.calls: List[Dict[str, Any]] = []

    def place(self, ticker, side, qty, price, reason, session_date="", dry_run=None, extra=None):
        self.calls.append({"ticker": ticker, "side": side, "qty": qty, "price": price})
        return {"status": "DRY_RUN", "ticker": ticker, "side": side, "qty": qty, "price": price}

    def account_state(self, dry_run=None, refresh_min=0.0):
        return {"found": False, "reason": "probe"}


def account_state() -> Dict[str, float]:
    with open(LIVE, "r", encoding="utf-8") as f:
        live = json.load(f)
    return {"equity_usd": float(live.get("current_total_equity_usd") or 0.0),
            "cash_usd": float(live.get("cash_deposit_usd") or 0.0),
            "as_of": live.get("last_synced")}


def prices() -> Dict[str, float]:
    with open(SNAPSHOT, "r", encoding="utf-8") as f:
        snap = json.load(f)
    out = {}
    for tk in UNIVERSE:
        q = (snap.get("quotes") or {}).get(tk) or {}
        px = q.get("last") or q.get("close")
        if px:
            out[tk] = round(float(px), 2)
    return out


def atr_pct() -> Dict[str, float]:
    """캐시된 일봉으로 ATR14/가격을 계산한다(없으면 보수적 기본값)."""
    fallback = {"SOXL": 22.0, "TQQQ": 7.7, "SOXS": 17.1, "SQQQ": 6.8}
    try:
        d = pd.read_pickle(DAILY_CACHE)
        frames = d.get("frames") or {}
        out = {}
        for tk in UNIVERSE:
            df = frames.get(tk)
            if df is None or len(df) < 20:
                out[tk] = fallback[tk]
                continue
            h, l, c = df["High"], df["Low"], df["Close"]
            pc = c.shift(1)
            tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
            atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
            out[tk] = round(atr / float(c.iloc[-1]) * 100, 2)
        return out
    except Exception:
        return fallback


def budget_table(policy: Dict[str, Any], acct: Dict[str, float],
                 risk_scale: float, vix_now: float) -> List[Dict[str, Any]]:
    rows = []
    for vix in VIX_GRID:
        vs = execution_policy.vix_scale(vix, policy)
        info = execution_policy.entry_budget(acct["equity_usd"], acct["cash_usd"], policy,
                                             vs * risk_scale)
        rows.append({"vix": vix, "vix_scale": vs, "risk_scale": risk_scale,
                     "budget_usd": info["budget_usd"], "binding": info["binding"],
                     "components": info["components"], "vol_curve": [vs * risk_scale],
                     "floor_usd": info.get("floor_usd"),
                     "below_floor": info.get("below_floor")})
    return rows


def sizing_probe(policy: Dict[str, Any], acct: Dict[str, float], px: Dict[str, float],
                 atr: Dict[str, float], vix: float, drop_closed: bool = False,
                 tickers: List[str] = None) -> List[Dict[str, Any]]:
    """신호를 주입해 4종목의 실제 매수 수량을 산출한다."""
    params = strategy_evolver.load_params()
    market = {
        "clock": {"now_kst": "2026-11-18 01:00:00", "phase": "REGULAR",
                  "session_date": "2026-11-17", "next_open_kst": "2026-11-17 23:30 KST"},
        "quotes": {tk: {"intraday_price": v} for tk, v in px.items()},
        "techs": {tk: {"atr_pct": a} for tk, a in atr.items()},
        "daily": {}, "snapshot": {}, "vix": vix,
    }
    trader = AutonomousTrader(policy=policy, router=DryRouter(), dry_run=True, market=market)
    st = trader.load_state("2026-11-17")
    # 실전 상태 대신 깨끗한 세션 상태를 쓴다(오늘 주문 이력이 게이트를 먹지 않도록).
    st.update({"entries": [], "orders": [], "pending": [], "halted": False,
               "halt_reason": None, "cooldowns": {}, "realized_pnl_usd": 0.0})
    with open(os.path.join(BASE_DIR, "data", "active_brackets.json"), "r", encoding="utf-8") as f:
        brackets = json.load(f)
    if drop_closed:
        brackets = {k: v for k, v in brackets.items() if not (isinstance(v, dict) and v.get("closed"))}

    def candidates(*_a, **_k):
        cands = []
        for tk in (tickers or UNIVERSE):
            if tk in ("SOXS", "SQQQ"):
                cands.append({"source": "BEAR_ALPHA_HEDGE", "ticker": tk,
                              "signal": {"vix": vix, "confirmations": [1, 2]},
                              "why": "프로브 — BEAR ALPHA 게이트", "atr_pct": atr[tk],
                              "notional_cap": HEDGE_NOTIONAL_CAP})
            else:
                cands.append({"source": "DIP_SWING_DAILY", "ticker": tk,
                              "signal": {"rsi2": 12.0, "pct_b": 0.05, "rvol": 1.4},
                              "why": "프로브 — DIP_SWING 신호", "atr_pct": atr[tk]})
        return cands

    trader.entry_candidates = candidates
    actions = trader.entries(st, brackets, market,
                             {"equity_usd": acct["equity_usd"], "cash_usd": acct["cash_usd"]},
                             params)
    by_ticker = {a["ticker"]: a for a in actions}
    rows = []
    for tk in (tickers or UNIVERSE):
        act = by_ticker.get(tk)
        sd = (act or {}).get("sizing") or {}
        rows.append({
            "ticker": tk, "price": px[tk], "atr_pct": atr[tk],
            "qty": int(act.get("qty") or 0) if act else 0,
            "notional_usd": sd.get("notional_usd") or 0.0,
            "sl_pct": sd.get("sl_pct"),
            "risk_usd": sd.get("risk_usd"),
            "risk_cap_usd": sd.get("risk_cap_usd"),
            "budget_usd": sd.get("budget_usd"),
            "min_one_share": bool(sd.get("min_one_share")),
            "override": sd.get("override"),
            "limit_price": act.get("price") if act else None,
            "deadlock": act is None,
            "skip_events": [e for e in trader.events
                            if e.get("kind") in ("ENTRY_SKIP", "ENTRY_BLOCKED_GLOBAL",
                                                 "ENTRY_BLOCKED_OPENING")],
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="결과를 저장할 경로")
    args = ap.parse_args()

    policy = execution_policy.load_policy()
    acct = account_state()
    px = prices()
    atr = atr_pct()
    params = strategy_evolver.load_params()
    risk_scale = float(params.get("risk_scale") or 1.0)

    snap_vix = 0.0
    try:
        with open(SNAPSHOT, "r", encoding="utf-8") as f:
            snap_vix = float(((json.load(f).get("quotes") or {}).get("^VIX") or {}).get("last") or 0.0)
    except Exception:
        snap_vix = 0.0

    desc = execution_policy.describe(policy, acct["equity_usd"], acct["cash_usd"],
                                     snap_vix, extra_scale=risk_scale)
    budgets = budget_table(policy, acct, risk_scale, snap_vix)
    sizing_now = sizing_probe(policy, acct, px, atr, snap_vix)
    sizing_now_clean = sizing_probe(policy, acct, px, atr, snap_vix, drop_closed=True)
    sizing_calm = sizing_probe(policy, acct, px, atr, 17.5, drop_closed=True)
    # 종목별 단독 프로브 — 보유종목 한도(4종목)에 걸리지 않은 순수 사이징 결과
    sizing_isolated = []
    for tk in UNIVERSE:
        sizing_isolated.extend(sizing_probe(policy, acct, px, atr, snap_vix, drop_closed=True,
                                            tickers=[tk]))
        sizing_isolated.extend(sizing_probe(policy, acct, px, atr, 17.5, drop_closed=True,
                                            tickers=[tk]))

    out = {
        "account": acct,
        "prices": px,
        "atr_pct": atr,
        "vix": snap_vix,
        "risk_scale": risk_scale,
        "guardrails": desc["guardrails"],
        "daily_loss_limit_usd": desc["daily_loss_limit_usd"],
        "entry_budget_now": desc["entry_budget"],
        "budget_by_vix": budgets,
        "sizing": {"vix_now": sizing_now, "vix_now_clean": sizing_now_clean,
                   "vix_17_5": sizing_calm, "isolated": sizing_isolated},
    }

    print("=" * 78)
    print(f"계좌: 총자산 ${acct['equity_usd']:.2f} · 예수금 ${acct['cash_usd']:.2f} "
          f"(as of {acct['as_of']}) · VIX {snap_vix:.2f} · risk_scale {risk_scale}")
    print("@VIX 시세 " + " · ".join(f"{k} ${v}" for k, v in px.items()))
    print("ATR14 " + " · ".join(f"{k} {v}%" for k, v in atr.items()))
    print("-" * 78)
    g = desc["guardrails"]
    for key in ("entry_budget_pct_of_cash", "entry_budget_pct_of_equity", "entry_budget_cap_usd",
                "entry_budget_floor_usd", "cash_reserve_floor_usd", "risk_per_trade_pct",
                "max_open_positions", "max_entries_per_session"):
        print(f"  {key:32s} {g.get(key)}")
    print("-" * 78)
    print(f"{'VIX':>6} {'vix×risk':>9} {'실효예산':>10} {'binding':>18}  구성(cash/equity/cap/현금여유)")
    for r in budgets:
        c = r["components"]
        print(f"{r['vix']:>6.2f} {r['vix_scale'] * risk_scale:>9.3f} {r['budget_usd']:>10.2f} "
              f"{r['binding']:>18}  {c['cash_pct']:.2f}/{c['equity_pct']:.2f}/"
              f"{c['abs_cap']:.2f}/{c['cash_after_reserve']:.2f}")
    print("-" * 78)
    print("-" * 78)
    print("[종목별 단독 신호 주입 — 현재 VIX / VIX 17.5]")
    print(f"  {'종목':6} {'가격':>7} {'VIX':>6} {'qty':>4} {'명목':>8} {'손절%':>6} {'리스크$':>8} {'1주리스크$':>10}  판정")
    for i, r in enumerate(sizing_isolated):
        vix_label = snap_vix if i % 2 == 0 else 17.5
        one_share_risk = r["price"] * float(r["sl_pct"] or 0) / 100.0
        verdict = "교착(0주)" if r["deadlock"] else ("1주 최소보장" if r.get("min_one_share") else "주문 가능")
        print(f"  {r['ticker']:6} {r['price']:>7.2f} {vix_label:>6.2f} {r['qty']:>4} "
              f"{float(r['notional_usd'] or 0):>8.2f} {float(r['sl_pct'] or 0):>6.2f} "
              f"{float(r['risk_usd'] or 0):>8.2f} {one_share_risk:>10.2f}  {verdict}")
    print()

    for label, rows in (("현재 VIX · 동시 신호(보유 한도 포함)", sizing_now),
                        ("현재 VIX · 종료 브래킷 제외(예산 순수효과)", sizing_now_clean),
                        ("VIX 17.5 · 종료 브래킷 제외", sizing_calm)):
        print(f"[{label}] 신호 주입 결과")
        print(f"  {'종목':6} {'가격':>7} {'qty':>4} {'명목':>8} {'손절%':>6} {'리스크$':>8} {'한도$':>8}  판정")
        for r in rows:
            verdict = "교착(주문 없음)" if r["deadlock"] else "주문 가능"
            print(f"  {r['ticker']:6} {r['price']:>7.2f} {r['qty']:>4} "
                  f"{float(r['notional_usd'] or 0):>8.2f} "
                  f"{float(r['sl_pct'] or 0):>6.2f} {float(r['risk_usd'] or 0):>8.2f} "
                  f"{float(r['risk_cap_usd'] or 0):>8.2f}  {verdict}")
        reasons = [f"{e.get('ticker')}: {e.get('reason')}" for e in rows[0]["skip_events"]] if rows else []
        if reasons:
            print("  스킵: " + " / ".join(reasons))
        print()

    if args.json:
        path = args.json if os.path.isabs(args.json) else os.path.join(BASE_DIR, args.json)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"저장: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
