#!/usr/bin/env python3
"""Offline, audit-only 10-agent performance feedback.

Reward is *confirmed realized USD P&L* from recorded closes, never unrealized
marks or backtest profit. Historical imports are reported separately. This is
a contextual-bandit-style candidate ranking, NOT live RL policy deployment.
No strategy/approval/config parameter is changed by this script.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DATA = os.path.join(BASE, "data")
LEDGER = os.path.join(DATA, "strategy_trade_ledger.json")
BUS = os.path.join(DATA, "agent_collab_bus.json")
OUT = os.path.join(DATA, "agent_performance.json")


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def summarize(ledger: dict, bus: dict) -> dict:
    trades = [t for t in (ledger.get("trades") or [])
              if isinstance(t, dict) and t.get("pnl_usd") is not None]
    # Imported legacy records are not independently verifiable from this dataset.
    verified = [t for t in trades if t.get("source") != "ledger_import"
                and t.get("entry") is not None and t.get("exit") is not None
                and float(t.get("qty") or 0) > 0]
    by_strategy = defaultdict(list)
    for t in verified:
        by_strategy[str(t.get("source") or "unknown")].append(float(t["pnl_usd"]))
    strategies = []
    for name, values in sorted(by_strategy.items()):
        n = len(values)
        avg = sum(values) / n
        var = sum((v - avg) ** 2 for v in values) / max(1, n - 1)
        # An uncertainty-penalized *offline candidate* score, not a trade rule.
        lcb = avg - 1.96 * math.sqrt(var / max(n, 1))
        strategies.append({"source": name, "confirmed_closes": n,
                           "realized_usd": round(sum(values), 2),
                           "mean_reward_usd": round(avg, 4),
                           "conservative_score_usd": round(lcb, 4),
                           "eligible_for_review": n >= 20,
                           "auto_deployed": False})
    state = bus.get("agent_state") or {}
    agents = [{"agent_id": i, "latest_report": (state.get(str(i)) or {}).get("line"),
               "reported_at_kst": (state.get(str(i)) or {}).get("ts_kst"),
               "desk": "research" if i <= 5 else "validation" if i <= 8 else "execution"}
              for i in range(1, 11)]
    return {"generated_at_kst": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
            "learning": "offline_conservative_contextual_bandit_candidate_review",
            "mode": "PROPOSAL_ONLY",
            "auto_deploy": False,
            "reward_basis": "confirmed realized USD P&L, not mark-to-market",
            "confirmed_trades": len(verified),
            "historical_imports_excluded": len(trades) - len(verified),
            "strategies": strategies,
            "candidate": (max((s for s in strategies if s["eligible_for_review"]),
                               key=lambda s: s["conservative_score_usd"], default=None)),
            "policy_change": "NONE: OOS and drawdown validation required before approval",
            "agents": agents,
            "team_score": {"latest_reports": sum(bool(a["latest_report"]) for a in agents),
                           "realized_pnl_usd": round(sum(float(t["pnl_usd"]) for t in verified), 2),
                           "allocation_note": "Portfolio P&L is shared, not attributed to ten independent employees"}}


def main() -> int:
    previous = _read(OUT, {})
    bus = _read(BUS, {})
    report = summarize(_read(LEDGER, {}), bus)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    # Broadcast *newly confirmed* portfolio reward to all ten agents, not
    # ten fictitious individual profits. Keep the shared feedback auditable.
    score = report["team_score"]["realized_pnl_usd"]
    if report["confirmed_trades"] > 0 and (
        report["confirmed_trades"] != previous.get("confirmed_trades")
        or score != (previous.get("team_score") or {}).get("realized_pnl_usd")
    ):
        from core import collab_bus
        doc = collab_bus.load()
        collab_bus.post(
            doc, 10, "집행·성과 리뷰", "FEEDBACK", "실현손익 기반 10인 공통 성과",
            (f"체결 검증 청산 {report['confirmed_trades']}건 · 누적 실현손익 "
             f"${score:.2f} · 전략 변경 PROPOSAL_ONLY. "
             "모든 데스크는 다음 주기 가설·위험관리 검토에 반영."),
            to=list(range(1, 11)),
            metrics={"confirmed_closes": report["confirmed_trades"],
                     "realized_usd": score, "auto_deployed": False})
        collab_bus.save(doc)
    print(f"[agent-performance] {report['team_score']['latest_reports']}/10 latest reports; "
          f"{report['confirmed_trades']} confirmed trades; proposed policy only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
