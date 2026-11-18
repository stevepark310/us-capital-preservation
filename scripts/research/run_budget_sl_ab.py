"""A/B — 1회 예산($44.80 → $150)과 손절 클램프(8% → 15%)의 효과 실측.

2026-11-18 사용자 지시("1회 예산을 대폭 늘려")로 바꾼 두 파라미터가
**기대값을 훼손하지 않는지** 같은 데이터·같은 마찰(왕복 0.24%)로 비교한다.

- 엔진과 동일한 규칙: `scripts/research/run_ab3.chandelier_sim`
  (다음 세션 시가 진입, 0.75×ATR 초기 손절 + `sl_max_pct` 클램프, 샹들리에 트레일, 최대 3세션)
- 표본: yfinance 일봉 캐시(2년·1년), 정수주(`qty = ⌊budget/price⌋`)
- 경로 가정: 일봉 저가로 손절 터치 판정(보수적)

    python3 scripts/research/run_budget_sl_ab.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_ab3 import chandelier_sim  # noqa: E402

OUT = os.path.join(BASE_DIR, "data", "budget_sl_ab_20261118.json")
SYMBOLS = ["SOXL", "TQQQ"]


def summarize(trades: List[Dict]) -> Dict[str, float]:
    if not trades:
        return {"n": 0}
    rets = [t["ret_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    return {
        "n": len(trades),
        "win_pct": round(len(wins) / len(trades) * 100, 1),
        "ev_pct": round(sum(rets) / len(rets), 3),
        "pf": round(pf, 2) if pf is not None else None,
        "pnl_usd": round(sum(t["pnl_usd"] for t in trades), 2),
        "worst_pct": round(min(rets), 2),
        "best_pct": round(max(rets), 2),
        "stop_share_pct": round(sum(1 for t in trades if t["reason"] == "STOP") / len(trades) * 100, 1),
        "avg_qty": round(sum(t["qty"] for t in trades) / len(trades), 2),
    }


def run() -> Dict:
    out: Dict = {"generated_at_kst": "2026-11-18", "cost": "round-trip 0.24%",
                 "engine": "chandelier_sim (0.75×ATR SL, trail 0.9×ATR, hold≤3 sessions)",
                 "budget_ab": {}, "sl_max_ab": {}}
    for period in ("2y", "1y"):
        for sym in SYMBOLS:
            key = f"{sym}_{period}"
            out["budget_ab"][key] = {
                "budget_44_8": summarize(chandelier_sim(sym, budget=44.8, sl_atr_mult=0.75,
                                                        sl_max_pct=15.0, period=period)),
                "budget_150": summarize(chandelier_sim(sym, budget=150.0, sl_atr_mult=0.75,
                                                      sl_max_pct=15.0, period=period)),
            }
            out["sl_max_ab"][key] = {
                "sl_max_8": summarize(chandelier_sim(sym, budget=150.0, sl_atr_mult=0.75,
                                                     sl_max_pct=8.0, period=period)),
                "sl_max_15": summarize(chandelier_sim(sym, budget=150.0, sl_atr_mult=0.75,
                                                      sl_max_pct=15.0, period=period)),
            }
    return out


def main() -> int:
    res = run()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("== 예산 A/B (SL 클램프 15% 고정) ==")
    for key, v in res["budget_ab"].items():
        a, b = v["budget_44_8"], v["budget_150"]
        print(f"{key:10} $44.8: n={a['n']:>3} EV {a['ev_pct']:>6}% PF {a['pf']} 손익 ${a['pnl_usd']:>7}"
              f"  →  $150: n={b['n']:>3} EV {b['ev_pct']:>6}% PF {b['pf']} 손익 ${b['pnl_usd']:>7}")
    print("== 손절 클램프 A/B (예산 $150 고정) ==")
    for key, v in res["sl_max_ab"].items():
        a, b = v["sl_max_8"], v["sl_max_15"]
        print(f"{key:10} 8%: n={a['n']:>3} EV {a['ev_pct']:>6}% PF {a['pf']} 승률 {a['win_pct']:>4}% "
              f"손절비중 {a['stop_share_pct']:>4}%  →  15%: n={b['n']:>3} EV {b['ev_pct']:>6}% "
              f"PF {b['pf']} 승률 {b['win_pct']:>4}% 손절비중 {b['stop_share_pct']:>4}%")
    print(f"저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
