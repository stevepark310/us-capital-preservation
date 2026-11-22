#!/usr/bin/env python3
"""
Cloud cycle — 피클 앱·토큰과 무관하게 GitHub Actions에서 도는 상시 자율 실행기.

두 가지 모드
------------
    python3 scripts/cloud_cycle.py market   # 장중 10분: 집행·청산·원장·상태
    python3 scripts/cloud_cycle.py pulse    # 24/7 30분: 협업·빌드업·대시보드

`market`은 미국 정규장(서머타임 포함)일 때만 실제로 돌고, 그 밖의 시간에는
`SKIPPED_OFF_HOURS`로 끝난다(크론 창을 넓게 잡아도 주문이 나가지 않는다).

모든 실행은 끝에 `data/cloud_status.json`을 갱신한다 — 대시보드가 '클라우드가
지금도 돌고 있는가'를 이 파일의 시각으로 판단한다(피클이 꺼져 있어도 갱신된다).

주문 경로
---------
러너에서는 NH Open API가 직접 열려 있으므로 `OrderRouter`가 `_try_direct`로 바로
발주한다(중첩 workflow_dispatch 없음). 실주문 여부는 `config/swing_approval.json`
`autonomous_execution.enabled`와 `config/live_execution_gate.json`의 enabled=true를
모두 만족해야 실주문을 수행한다(기본 false). `CUBE_CLOUD_DRY_RUN=1`은 항상 실주문을 막는다.
정규장에서도 실계좌 조회·보유수량 검증이 실패하면 집행을 차단한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

DATA = os.path.join(BASE_DIR, "data")
CLOUD_PATH = os.path.join(DATA, "cloud_status.json")

MARKET_CRON = "4-59/10 13-21 * * 1-5"
PULSE_CRON = "9-59/10 * * * *"
LIVE_GATE_PATH = os.path.join(BASE_DIR, "config", "live_execution_gate.json")


def now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def now_str() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def run(name: str, cmd: List[str], timeout: int = 900) -> Dict[str, Any]:
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=BASE_DIR, text=True, capture_output=True, timeout=timeout)
        out, err, rc = p.stdout or "", p.stderr or "", p.returncode
    except subprocess.TimeoutExpired as e:
        out, err, rc = (e.stdout or ""), f"TIMEOUT {timeout}s", 124
    dt = round(time.time() - t0, 1)
    tail = (out.strip().splitlines() or [""])[-1]
    print(f"  [{name}] {dt}s {'OK' if rc == 0 else f'FAILED({rc})'} — {tail[:160]}")
    if rc != 0:
        print("    " + "\n    ".join((err.strip().splitlines() or [""])[-6:]))
    return {"name": name, "seconds": dt, "rc": rc, "ok": rc == 0, "tail": tail[:300]}


def write_status(mode: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    doc = _load(CLOUD_PATH, {}) or {}
    doc.setdefault("version", "1.0")
    doc["updated_at_kst"] = now_str()
    doc["schedule"] = {"market_cron_utc": MARKET_CRON, "pulse_cron_utc": PULSE_CRON,
                       "market_note": "UTC 13–21시 = KST 22시~익일 07시 (EDT/EST 모두 커버). 정규장 판정은 market_clock.",
                       "pulse_note": "24/7 매 10분"}
    doc["stale_after_min"] = {"market": 25, "pulse": 75}
    if mode == "market":
        doc["market"] = payload
    else:
        doc["pulse"] = payload
    run_meta = {
        "workflow": os.getenv("GITHUB_WORKFLOW") or ("local" if not os.getenv("GITHUB_ACTIONS") else "unknown"),
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "event": os.getenv("GITHUB_EVENT_NAME"),
        "attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "runner": "github_actions" if os.getenv("GITHUB_ACTIONS") == "true" else "local",
        "sha": (os.getenv("GITHUB_SHA") or "")[:8] or None,
    }
    doc["last_run"] = dict(run_meta, at_kst=now_str(), mode=mode, result=payload.get("result"))
    _save(CLOUD_PATH, doc)
    return doc


# --------------------------------------------------------------------- market
def _live_account_preflight(sell_only: bool = False) -> Dict[str, Any]:
    """Fail closed unless the live NH account matches the checked-in ledger.

    sell_only(매도 전용 세션)에서는 NH 잔고가 **읽히는 것**이 조건이고, 원장과의
    보유수량 일치까지 요구하지 않는다 — 책 한 권이 어긋났다고 사용자가 지시한
    청산을 막으면 3배 인버스 포지션이 그대로 넘어간다. 수량·매도가능 여부는 주문
    직전 `order_bridge._live_pretrade_check`가 NH에서 다시 확인한다(이중 확인 유지).
    """
    required = ("NH_APP_KEY", "NH_APP_SECRET", "NH_ACT_NO")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        return {"ok": False, "reason": "NH API credentials missing: " + ", ".join(missing)}
    try:
        from execution.nh_trader import NHBroker
        account = NHBroker(dry_run=False).get_portfolio_state(include_raw=False)
        if str(account.get("rsp_cd")) != "00166":
            return {"ok": False, "reason": "NH balance query unsuccessful"}
        if account.get("equity") is None or account.get("cash") is None:
            return {"ok": False, "reason": "NH balance response missing equity/cash"}
        if float(account["equity"]) <= 0 or float(account["cash"]) < 0:
            return {"ok": False, "reason": "NH balance contains invalid equity/cash"}
        live = _load(os.path.join(DATA, "live_portfolio.json"), {})
        if live.get("api_status") != "CONNECTED":
            return {"ok": False, "reason": "Local account ledger is not marked CONNECTED"}
        broker_positions = {t: int(p.get("qty") or 0) for t, p in
                            (account.get("positions") or {}).items() if int(p.get("qty") or 0) > 0}
        ledger_positions = {p.get("ticker"): int(p.get("qty") or 0) for p in
                            (live.get("positions") or []) if int(p.get("qty") or 0) > 0}
        if broker_positions != ledger_positions:
            mismatch = {"nh_positions": broker_positions, "ledger_positions": ledger_positions}
            if not sell_only:
                return {"ok": False, "reason": "NH holdings differ from ledger; reconcile before trading",
                        **mismatch}
            return {"ok": True, "sell_only": True, "warning": "NH holdings differ from ledger",
                    "nh_positions": broker_positions, "ledger_positions": ledger_positions,
                    "nh_equity_usd": round(float(account["equity"]), 2),
                    "nh_cash_usd": round(float(account["cash"]), 2)}
        return {"ok": True, "nh_positions": broker_positions,
                "nh_equity_usd": round(float(account["equity"]), 2),
                "nh_cash_usd": round(float(account["cash"]), 2)}
    except Exception as exc:
        return {"ok": False, "reason": "NH preflight error: " + type(exc).__name__}


def _team_preflight() -> Dict[str, Any]:
    """Demand current duty notes from every agent and no collaboration hard-lock failures."""
    try:
        collab = _load(os.path.join(DATA, "agent_collab_bus.json"), {})
        state = collab.get("agent_state") or {}
        now = now_kst()
        missing = []
        for agent_id in range(1, 11):
            record = state.get(str(agent_id)) or {}
            stamp = str(record.get("ts_kst") or "")
            try:
                age = (now - datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")).total_seconds()
            except (ValueError, TypeError):
                age = float("inf")
            if not record.get("line") or not -60 <= age <= 900:
                missing.append(agent_id)
        cycle = _load(os.path.join(DATA, "collab_cycle.json"), {})
        lock = cycle.get("hard_lock") or {}
        if missing or lock.get("ok") is not True:
            return {"ok": False, "reason": "Missing/stale agent reports or failed risk hard-lock",
                    "missing_agents": missing, "hard_lock": lock}
        return {"ok": True, "agents": 10, "cycle": cycle.get("cycle"),
                "validation": (cycle.get("validation") or {}).get("status")}
    except Exception as exc:
        return {"ok": False, "reason": "Team validation error: " + type(exc).__name__}


def market_cycle(dry_run: Optional[bool] = None) -> Dict[str, Any]:
    from core import market_clock, session_routing
    clock = market_clock.resolve()
    t0 = time.time()
    from core.live_session_gate import check_live_gate
    gate = _load(LIVE_GATE_PATH, {})
    plan = session_routing.status(session_routing.load_policy())
    auto_phases = session_routing.auto_phases(plan)
    gate_check = check_live_gate(gate, clock, plan=plan)
    live_enabled = gate_check["ok"]
    sell_only = bool(gate_check.get("sell_only"))
    # 대시보드에는 '이 세션에 매도 전용으로 열려 있는가'를 보여 준다 — 정규장 밖에서
    # 게이트가 닫혀 있을 때도 설정 의도가 보이게(세션 판정은 live_gate_enabled가 답한다).
    sell_only_shown = sell_only if live_enabled else bool(gate.get("sell_only") is True)
    requested_dry = bool(dry_run if dry_run is not None else os.getenv("CUBE_CLOUD_DRY_RUN") == "1")
    base: Dict[str, Any] = {"last_cycle_kst": now_str(), "phase": clock.get("phase"),
                            "session_date": clock.get("session_date"),
                            "kst_shift_min": clock.get("kst_shift_min"),
                            "next_open_kst": clock.get("next_open_kst"),
                            "order_type": gate_check.get("order_type"),
                            "session_plan": {k: plan.get(k) for k in
                                             ("phases", "auto_phases", "order_types", "premarket_enabled",
                                              "premarket_auto_enabled", "premarket_manual_only",
                                              "manual_intervention_only", "after_hours_enabled",
                                              "blocked_reason", "auto_blocked_reason", "approved_by")},
                            "dry_run": requested_dry or not live_enabled,
                            "live_gate_enabled": live_enabled,
                            "live_gate_reason": gate_check.get("reason"),
                            "live_gate_sell_only": sell_only_shown,
                            "note": (f"실주문 비활성 — {gate_check.get('reason')}"
                                     if not live_enabled else
                                     ("오늘 미국장 기간한정 실주문 게이트 활성 · 매도 전용(신규 매수 0건)"
                                      if sell_only else
                                      f"오늘 {clock.get('phase')} 실주문 게이트 활성 · 호가유형 "
                                      f"{gate_check.get('order_type')}"))}
    if clock.get("phase") not in auto_phases:
        # 프리장 수동·지시 개입 전용(manual_intervention_only)이면 자동 사이클은 주문을
        # 만들지 않는다. 수동 개입은 `cloud_cycle.py intervene`(intervene_trade.py)로만.
        # 정규장 밖이라도 '연체된 강제청산'은 계산해서 남긴다 — 종전에는 SKIPPED로 끝나
        # 05:40 청산이 통째로 사라졌다(2026-11-19 SQQQ). 다음 정규장 첫 사이클이 닫는다.
        failsafe = {}
        try:
            from core import exit_failsafe
            brackets_now = _load(os.path.join(DATA, "active_brackets.json"), {}) or {}
            failsafe = exit_failsafe.record(brackets_now, clock)
        except Exception as exc:      # 상태 기록 실패가 사이클을 막지는 않는다
            failsafe = {"error": type(exc).__name__}
        manual_skip = bool(plan.get("premarket_manual_only")) and clock.get("phase") == "PRE_MARKET"
        result = "FAILSAFE_OVERDUE" if (failsafe.get("overdue") or
                                        (failsafe.get("cloud") or {}).get("critical")) else (
            "SKIPPED_PREMARKET_MANUAL" if manual_skip else "SKIPPED_OFF_HOURS")
        base.update({"result": result, "seconds": round(time.time() - t0, 1),
                     "failsafe": failsafe,
                     "note": (f"phase={clock.get('phase')} — {plan.get('auto_blocked_reason')}: "
                              f"자동 사이클 주문 없음. 수동·지시 개입은 cloud_cycle.py intervene(호가유형 61)."
                              if manual_skip else
                              f"phase={clock.get('phase')} — 정규장이 아니므로 주문은 하지 않습니다.")
                     if result in ("SKIPPED_PREMARKET_MANUAL", "SKIPPED_OFF_HOURS") else
                     f"phase={clock.get('phase')} — 연체 강제청산/감시 공백 "
                     f"{len(failsafe.get('overdue') or [])}건, 다음 정규장 즉시 청산 대상으로 기록."})
        write_status("market", base)
        print(f"[cloud] market {result} phase={clock.get('phase')} ({clock.get('now_kst')} KST)")
        return base

    stages: List[Dict[str, Any]] = []
    py = sys.executable
    # Confirm previous-cycle fills before comparing live NH holdings to the
    # ledger. Otherwise a successful trade makes the next preflight fail.
    pending = (_load(os.path.join(DATA, "autotrader_state.json"), {}) or {}).get("pending") or []
    if pending:
        stages.append(run("NH 체결 확정(주문 없음)", [py, "scripts/reconcile_confirmed_fills.py"], 240))
    stages.append(run("원장 동기화", [py, "scripts/sync_live_portfolio.py"], 300))
    stages.append(run("유령 포지션 점검", [py, "-m", "core.position_reconciler", "--apply"], 120))
    # Refresh all ten duty reports and validation before each market decision.
    stages.append(run("10인 업무보고", [py, "scripts/update_feed.py"], 360))
    stages.append(run("10인 10분 펄스 누적", [py, "scripts/run_10m_pulse.py", "--pulse-only", "--no-html"], 120))
    stages.append(run("10인 협업 검증", [py, "scripts/run_collab_cycle.py"], 180))
    if not all(stage["ok"] for stage in stages):
        base.update({"result": "BLOCKED_DEPENDENCY", "seconds": round(time.time() - t0, 1),
                     "stages": stages, "note": "원장/조정 실패 — 자동주문 실행 차단"})
        write_status("market", base)
        return base
    if not base["dry_run"]:
        team = _team_preflight()
        base["team_preflight"] = team
        if not team["ok"]:
            if not sell_only:
                base.update({"result": "BLOCKED_TEAM", "seconds": round(time.time() - t0, 1),
                             "stages": stages, "note": team["reason"]})
                write_status("market", base)
                return base
            # 매도 전용 세션: 10인 업무보고·협업 하드락은 청산의 전제가 아니다.
            # (포지션·매도가능 수량은 아래 NH preflight와 주문 직전 재확인이 한다.)
            base["team_preflight_advisory"] = team.get("reason")
        preflight = _live_account_preflight(sell_only=sell_only)
        base["preflight"] = preflight
        if not preflight["ok"]:
            base.update({"result": "BLOCKED_PREFLIGHT", "seconds": round(time.time() - t0, 1),
                         "stages": stages, "note": preflight["reason"]})
            write_status("market", base)
            return base
    auto_cmd = [py, "scripts/autotrader.py", "--dry-run" if base["dry_run"] else "--execute"]
    auto_step = run("무인 집행", auto_cmd, 780)
    stages.append(auto_step)
    if not auto_step["ok"]:
        base.update({"result": "BLOCKED_EXECUTION", "seconds": round(time.time() - t0, 1),
                     "stages": stages, "note": "무인 집행 단계 실패 — 후속 주문 단계 차단"})
        write_status("market", base)
        return base
    # 동일 브래킷을 두 엔진이 동시에 집행하지 않도록 보조 감시는 읽기 전용으로 둔다.
    stages.append(run("브래킷 감시(읽기 전용)", [py, "scripts/monitor_and_exit.py", "--dry-run"], 300))
    stages.append(run("원장 재동기화", [py, "scripts/sync_live_portfolio.py"], 300))
    stages.append(run("평단 계획 재생성", [py, "-m", "core.avg_anchor_engine"], 300))
    stages.append(run("유령 포지션 재점검", [py, "-m", "core.position_reconciler", "--apply"], 120))
    stages.append(run("집행 상태 최신화", [py, "scripts/autotrader.py", "--refresh-status"], 120))
    stages.append(run("성과·학습 후보 평가", [py, "scripts/agent_performance.py"], 120))
    stages.append(run("청산 페일세이프 점검", [py, "scripts/exit_watchdog.py", "--record"], 120))
    stages.append(run("퀀트 포트폴리오 위험 감시", [py, "scripts/quant_monitor.py"], 120))
    stages.append(run("대시보드 데이터 발행", [py, "scripts/build_dashboard.py", "--data-only"], 240))

    status = _load(os.path.join(DATA, "autotrader_status.json"), {}) or {}
    plan = _load(os.path.join(DATA, "avg_anchor_plan.json"), {}) or {}
    base.update({
        "result": "OK" if all(s["ok"] for s in stages) else "PARTIAL",
        "seconds": round(time.time() - t0, 1),
        "stages": stages,
        "execution": {"mode": status.get("mode"), "positions": status.get("positions") or {},
                      "exits": len(status.get("exits") or []), "entries": len(status.get("entries") or []),
                      "pending": len(status.get("pending_orders") or []),
                      "realized_today_usd": status.get("realized_pnl_today_usd"),
                      "budget_usd": ((status.get("policy") or {}).get("entry_budget") or {}).get("budget_usd"),
                      "next_gate": status.get("next_gate")},
        "ladders": [l.get("ticker") for l in (plan.get("ladders") or [])],
    })
    write_status("market", base)
    print(f"[cloud] market {base['result']} {base['seconds']}s · 보유 {len(base['execution']['positions'])} · "
          f"매도 {base['execution']['exits']} / 매수 {base['execution']['entries']}")
    return base


# --------------------------------------------------------------------- pulse
def pulse_cycle() -> Dict[str, Any]:
    t0 = time.time()
    stages: List[Dict[str, Any]] = []
    py = sys.executable
    stages.append(run("원장 동기화", [py, "scripts/sync_live_portfolio.py"], 300))
    stages.append(run("유령 포지션 점검", [py, "-m", "core.position_reconciler", "--apply"], 120))
    stages.append(run("집행 상태 최신화", [py, "scripts/autotrader.py", "--refresh-status"], 120))
    # 검증 데스크가 쓰는 2년 일봉 캐시를 하루 1회(20시간 초과 시) 갱신한다.
    stages.append(run("가격 캐시 갱신", [py, "scripts/refresh_price_cache.py"], 240))
    # CUBE-M8 8대 거시·자산 선행지표 — 3시간 이상 묵었을 때만 FRED·Yahoo를 다시 읽는다.
    stages.append(run("선행지표 동기화(M8)", [py, "scripts/sync_macro_leading.py", "--if-stale", "3", "--quiet"], 420))
    stages.append(run("10인 업무보고", [py, "scripts/update_feed.py"], 600))
    stages.append(run("협업·빌드업 사이클", [py, "scripts/run_collab_cycle.py"], 300))
    stages.append(run("성과·학습 후보 평가", [py, "scripts/agent_performance.py"], 120))
    stages.append(run("퀀트 포트폴리오 위험 감시", [py, "scripts/quant_monitor.py"], 120))
    stages.append(run("10인 10분 펄스 누적", [py, "scripts/run_10m_pulse.py", "--pulse-only", "--no-html"], 120))
    stages.append(run("대시보드 렌더", [py, "scripts/build_full_cube.py"], 300))
    base = {"last_cycle_kst": now_str(), "result": "OK" if all(s["ok"] for s in stages) else "PARTIAL",
            "seconds": round(time.time() - t0, 1), "stages": stages}
    collab = _load(os.path.join(DATA, "collab_cycle.json"), {}) or {}
    base["collab"] = {"cycle": collab.get("cycle"), "hypothesis": (collab.get("validation") or {}).get("hypothesis"),
                      "status": (collab.get("validation") or {}).get("status"),
                      "applied": (collab.get("validation") or {}).get("applied")}
    write_status("pulse", base)
    print(f"[cloud] pulse {base['result']} {base['seconds']}s · 협업 {base['collab']}")
    return base


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    mode = (args[0] if args else "").strip().lower()
    if mode == "market":
        return 0 if market_cycle().get("result") in ("OK", "SKIPPED_OFF_HOURS", "SKIPPED_PREMARKET_MANUAL",
                                                     "FAILSAFE_OVERDUE") else 1
    if mode == "pulse":
        return 0 if pulse_cycle().get("result") == "OK" else 1
    if mode == "intervene":
        # 즉시 개입 — 단일 진입점(scripts/intervene_trade.py)에 그대로 넘긴다.
        # 기본은 미리보기이고 실주문은 `--execute`를 붙여야 나간다.
        from scripts import intervene_trade
        return intervene_trade.main(args[1:])
    if mode == "status":
        print(json.dumps(_load(CLOUD_PATH, {}), ensure_ascii=False, indent=2))
        return 0
    print("usage: python3 scripts/cloud_cycle.py market|pulse|intervene|status")
    return 2


if __name__ == "__main__":
    sys.exit(main())
