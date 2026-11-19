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
`autonomous_execution.enabled`(true)와 `data/autotrader_halt.json` 부재로 결정된다.
`CUBE_CLOUD_DRY_RUN=1`을 주면 판단만 하고 주문하지 않는다(검증용).
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

MARKET_CRON = "*/10 13-21 * * 1-5"
PULSE_CRON = "*/30 * * * *"


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
                       "pulse_note": "24/7 매 30분"}
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
def market_cycle(dry_run: Optional[bool] = None) -> Dict[str, Any]:
    from core import market_clock
    clock = market_clock.resolve()
    t0 = time.time()
    base: Dict[str, Any] = {"last_cycle_kst": now_str(), "phase": clock.get("phase"),
                            "session_date": clock.get("session_date"),
                            "kst_shift_min": clock.get("kst_shift_min"),
                            "next_open_kst": clock.get("next_open_kst"),
                            "dry_run": bool(dry_run if dry_run is not None else os.getenv("CUBE_CLOUD_DRY_RUN") == "1")}
    if clock.get("phase") != "REGULAR":
        base.update({"result": "SKIPPED_OFF_HOURS", "seconds": round(time.time() - t0, 1),
                     "note": f"phase={clock.get('phase')} — 정규장이 아니므로 주문·집행을 하지 않습니다."})
        write_status("market", base)
        print(f"[cloud] market SKIPPED phase={clock.get('phase')} ({clock.get('now_kst')} KST)")
        return base

    stages: List[Dict[str, Any]] = []
    py = sys.executable
    stages.append(run("원장 동기화", [py, "scripts/sync_live_portfolio.py"], 300))
    stages.append(run("유령 포지션 점검", [py, "-m", "core.position_reconciler", "--apply"], 120))
    auto_cmd = [py, "scripts/autotrader.py", "--execute"]
    if base["dry_run"]:
        auto_cmd = [py, "scripts/autotrader.py", "--dry-run"]
    stages.append(run("무인 집행", auto_cmd, 780))
    mon_cmd = [py, "scripts/monitor_and_exit.py"] + ([] if base["dry_run"] else ["--execute"])
    stages.append(run("브래킷 감시", mon_cmd, 300))
    stages.append(run("원장 재동기화", [py, "scripts/sync_live_portfolio.py"], 300))
    stages.append(run("평단 계획 재생성", [py, "-m", "core.avg_anchor_engine"], 300))
    stages.append(run("유령 포지션 재점검", [py, "-m", "core.position_reconciler", "--apply"], 120))
    stages.append(run("집행 상태 최신화", [py, "scripts/autotrader.py", "--refresh-status"], 120))
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
    stages.append(run("10인 업무보고", [py, "scripts/update_feed.py"], 600))
    stages.append(run("협업·빌드업 사이클", [py, "scripts/run_collab_cycle.py"], 300))
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
        return 0 if market_cycle().get("result") in ("OK", "PARTIAL", "SKIPPED_OFF_HOURS") else 1
    if mode == "pulse":
        return 0 if pulse_cycle().get("result") in ("OK", "PARTIAL") else 1
    if mode == "status":
        print(json.dumps(_load(CLOUD_PATH, {}), ensure_ascii=False, indent=2))
        return 0
    print("usage: python3 scripts/cloud_cycle.py market|pulse|status")
    return 2


if __name__ == "__main__":
    sys.exit(main())
