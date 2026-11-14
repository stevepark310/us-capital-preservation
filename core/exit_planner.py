"""
CUBE Adaptive Exit Planner — fill-based bracket re-anchoring

Why this exists
---------------
The 11-14 SOXL scalp exposed two structural flaws in the fixed bracket:

1. Brackets were calculated off the *planned* price, not the *fill*. Order
   240539 was planned at $55.10 and the TP pair ($56.69 / $57.86) was set from
   that number, so any slippage silently moved the true reward/risk.
2. TP2 sold 100% of the remainder at a fixed +5.0%. SOXL printed $58.35 minutes
   after the $57.86 exit — the runner that the desk's own philosophy
   ("Run your winners") demands was never allowed to exist.

This planner computes every bracket from the realised fill, scales the target
width with the symbol's own ATR, ratchets the stop to breakeven + fees after
TP1, and splits the remainder into a runner that trails on a Chandelier
(ATR) and an EMA-20 stop — whichever is tighter, but never before a locked
profit floor.

All parameters are explicit and were validated in core/backtest_lab.py
(baseline vs upgraded); see data/1y_backtest_report.json.
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, List

ROUND_TRIP_FEE_PCT = 0.0024     # NH commission 0.09% + slippage 0.15% (round trip)
FEE_BUFFER_PCT = 0.0010         # extra cushion so the runner cannot scratch a loss


@dataclass
class ExitPlan:
    symbol: str
    side: str
    planned_price: float
    fill_price: float
    qty: int
    atr_pct: float
    tp1_pct: float
    tp2_pct: float
    sl_pct: float
    tp1_price: float
    tp2_price: float
    hard_sl_price: float
    breakeven_price: float
    tp1_qty: int
    tp2_qty: int
    runner_qty: int
    runner_trail_pct: float
    runner_mode: str
    time_stop_minutes: int
    slippage_bps: float
    fill_reanchored: bool
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class AdaptiveExitPlanner:
    """ATR-scaled, fill-anchored bracket constructor."""

    # Defaults tuned by the walk-forward grid in scripts/run_1y_backtest.py
    DEFAULTS = {
        "tp1_atr_mult": 0.45,
        "tp2_atr_mult": 0.95,
        "sl_atr_mult": 0.55,
        "trail_atr_mult": 0.90,
        "tp1_share": 0.34,
        "tp2_share": 0.33,
        "runner_share": 0.33,
        "tp1_min_pct": 1.0,
        "tp1_max_pct": 7.0,
        "tp2_min_pct": 2.2,
        "tp2_max_pct": 12.0,
        "sl_min_pct": 1.2,
        "sl_max_pct": 5.0,
    }

    def __init__(self, **overrides):
        self.params = dict(self.DEFAULTS)
        self.params.update({k: v for k, v in overrides.items() if v is not None})

    def plan(
        self,
        symbol: str,
        planned_price: float,
        fill_price: float,
        qty: int,
        atr_pct: float,
        sentiment_score: float = 50.0,
        regime_ok: bool = True,
        side: str = "LONG",
        hard_floor_price: Optional[float] = None,
        allow_runner: bool = True,
    ) -> ExitPlan:
        p = self.params
        fill_price = float(fill_price or planned_price)
        planned_price = float(planned_price or fill_price)
        atr_pct = float(atr_pct or 4.0)
        notes: List[str] = []

        slippage_bps = round((fill_price / planned_price - 1.0) * 10000, 1)
        fill_reanchored = abs(slippage_bps) >= 5.0
        if fill_reanchored:
            notes.append(f"체결가 기준 재산정: 계획 ${planned_price:.2f} → 체결 ${fill_price:.2f} ({slippage_bps:+.0f}bp)")

        # Target width is the symbol's own volatility, not a fixed percentage.
        tp1_pct = _clamp(p["tp1_atr_mult"] * atr_pct, p["tp1_min_pct"], p["tp1_max_pct"])
        tp2_pct = _clamp(p["tp2_atr_mult"] * atr_pct, p["tp2_min_pct"], p["tp2_max_pct"])
        sl_pct = _clamp(p["sl_atr_mult"] * atr_pct, p["sl_min_pct"], p["sl_max_pct"])

        # Sentiment tilt: strong news widens targets and the runner; bearish news tightens.
        if sentiment_score >= 75:
            tp1_pct *= 1.10
            tp2_pct *= 1.15
            sl_pct *= 0.95
            notes.append("센티먼트 75+ : 목표가 상향·러너 확대")
        elif sentiment_score <= 35:
            tp1_pct *= 0.80
            tp2_pct *= 0.75
            sl_pct *= 0.75
            notes.append("센티먼트 35- : 조기 익절·손절 축소")
        if not regime_ok:
            tp1_pct *= 0.85
            sl_pct *= 0.85
            notes.append("레짐 비우호: 목표·손절 동시 축소")

        tp1_pct, tp2_pct, sl_pct = round(tp1_pct, 2), round(tp2_pct, 2), round(sl_pct, 2)
        if tp2_pct <= tp1_pct:
            tp2_pct = round(tp1_pct + 1.0, 2)

        be_pct = (ROUND_TRIP_FEE_PCT + FEE_BUFFER_PCT) * 100.0  # 0.34%

        if side.upper() == "SHORT":
            sign = -1.0
        else:
            sign = 1.0
        tp1_price = round(fill_price * (1 + sign * tp1_pct / 100.0), 2)
        tp2_price = round(fill_price * (1 + sign * tp2_pct / 100.0), 2)
        hard_sl_price = round(fill_price * (1 - sign * sl_pct / 100.0), 2)
        breakeven_price = round(fill_price * (1 + sign * be_pct / 100.0), 2)

        # The user's standing stop (e.g. NVDA $150 close line) always wins if wider.
        if hard_floor_price is not None:
            if side.upper() == "LONG" and hard_floor_price < hard_sl_price:
                hard_sl_price = round(float(hard_floor_price), 2)
                notes.append(f"사용자 마지노선 ${hard_floor_price:.2f} 우선 적용(기계적 손절보다 넓음)")

        qty = max(1, int(qty))
        if qty == 1:
            tp1_qty, tp2_qty, runner_qty = 1, 1, 0  # single share: TP1 is the whole position
            tp2_qty = 0
            notes.append("1주 포지션: TP1 전량 청산(러너 불가)")
        elif qty == 2:
            tp1_qty, tp2_qty, runner_qty = 1, 0, 1
            notes.append("2주 포지션: TP1 1주 + 러너 1주")
        else:
            tp1_qty = max(1, int(round(qty * p["tp1_share"])))
            tp2_qty = max(1, int(round(qty * p["tp2_share"])))
            runner_qty = max(0, qty - tp1_qty - tp2_qty)
        if not allow_runner:
            tp2_qty += runner_qty
            runner_qty = 0

        runner_trail_pct = round(max(1.0, p["trail_atr_mult"] * atr_pct * 0.5), 2)
        runner_mode = ("CHANDELIER_ATR + EMA20 중 더 타이트한 쪽, 최소 +1.5% 이익 잠금"
                       if runner_qty > 0 else "N/A")

        return ExitPlan(
            symbol=symbol, side=side.upper(), planned_price=round(planned_price, 2),
            fill_price=round(fill_price, 2), qty=qty, atr_pct=round(atr_pct, 2),
            tp1_pct=tp1_pct, tp2_pct=tp2_pct, sl_pct=sl_pct,
            tp1_price=tp1_price, tp2_price=tp2_price, hard_sl_price=hard_sl_price,
            breakeven_price=breakeven_price,
            tp1_qty=tp1_qty, tp2_qty=tp2_qty, runner_qty=runner_qty,
            runner_trail_pct=runner_trail_pct, runner_mode=runner_mode,
            time_stop_minutes=90, slippage_bps=slippage_bps,
            fill_reanchored=fill_reanchored, notes=notes,
        )

    # ------------------------------------------------------------------ runner trail
    def runner_stop(self, fill_price: float, highest_high: float, atr_value: float,
                    ema20: float, current_price: Optional[float] = None,
                    side: str = "LONG") -> Dict[str, Any]:
        """Chandelier vs EMA20 → tighter stop, but never below the locked profit floor.

        Any candidate at or above the current price is invalid as a stop and is dropped.
        """
        p = self.params
        chandelier = highest_high - p["trail_atr_mult"] * atr_value
        locked_floor = fill_price * 1.015
        ceiling = (current_price if current_price else highest_high) * 0.998
        candidates = {"chandelier": round(min(chandelier, ceiling), 2),
                      "ema20": round(min(ema20, ceiling), 2)}
        trail = max(candidates.values())
        stop = max(trail, round(locked_floor, 2))
        source = "chandelier" if candidates["chandelier"] >= candidates["ema20"] else "ema20"
        if stop == round(locked_floor, 2):
            source = "locked_profit_floor"
        return {"stop": round(stop, 2), "source": source, "candidates": candidates,
                "locked_floor": round(locked_floor, 2)}


def plan_from_bracket(bracket: Dict[str, Any], atr_pct: float, sentiment_score: float = 50.0,
                      hard_floor_price: Optional[float] = None) -> ExitPlan:
    """Rebuild a live bracket (active_brackets.json) from its realised entry."""
    return AdaptiveExitPlanner().plan(
        symbol=bracket.get("ticker") or bracket.get("symbol") or "?",
        planned_price=bracket.get("planned_price", bracket.get("entry_price")),
        fill_price=bracket.get("entry_price"),
        qty=int(bracket.get("bought_qty", bracket.get("qty", 1)) or 1),
        atr_pct=atr_pct,
        sentiment_score=sentiment_score,
        hard_floor_price=hard_floor_price,
        allow_runner=int(bracket.get("runner_qty", 0) or 0) > 0,
    )


if __name__ == "__main__":
    pl = AdaptiveExitPlanner()
    plan = pl.plan("SOXL", planned_price=55.10, fill_price=55.32, qty=3, atr_pct=12.0, sentiment_score=22)
    import json
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    print(pl.runner_stop(55.32, 58.35, 6.5, 60.1))
