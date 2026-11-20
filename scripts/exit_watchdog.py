#!/usr/bin/env python3
"""
강제청산 워치독 — 클라우드 크론이 멈춰도 장마감 전 청산이 사라지지 않게 하는 보완 경로.

쓰임
----
    python3 scripts/exit_watchdog.py --record     # 상태만 기록(주문·부작용 없음)
    python3 scripts/exit_watchdog.py --check      # 조치 필요하면 exit 0, 아니면 1
    python3 scripts/exit_watchdog.py --dispatch   # 필요할 때만 GitHub 시장 사이클을 깨운다

`--check`는 Pickle 로컬 스케줄(예: 세션 중 10분 간격)의 `check.sh`가 쓴다. 조건이
맞을 때만 run이 열린다. `--dispatch`는 `gh workflow run`으로
`market_autotrade.yml`을 실행한다(러너에서 NH API가 열려 있다).

무엇을 '조치 필요'로 보는가
--------------------------
1. `force_exit_kst`(+10분 버퍼)가 지났는데 열려 있는 포지션 (`overdue`)
2. 정규장인데 마지막 시장 사이클이 45분 넘게 없다 (`cloud.critical`)

둘 다 `data/exit_failsafe.json`에 남는다. 주문은 이 스크립트가 내지 않는다 —
`core/autotrader.py`만 낸다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import exit_failsafe, market_clock  # noqa: E402

BRACKETS = os.path.join(BASE_DIR, "data", "active_brackets.json")


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def action_needed(doc: dict) -> bool:
    return bool(doc.get("overdue") or (doc.get("cloud") or {}).get("critical"))


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    clock = market_clock.resolve()
    brackets = _load(BRACKETS)
    # --check는 읽기 전용(스케줄 체크 환경은 state/ 밖을 쓸 수 없다).
    doc = exit_failsafe.record(brackets, clock, persist=("--check" not in args))

    if "--dispatch" in args:
        if not action_needed(doc):
            print("[watchdog] 조치 불필요 — 디스패치 생략")
            return 1
        cmd = ["gh", "workflow", "run", "market_autotrade.yml",
               "-R", "stevepark310/us-capital-preservation"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            ok = p.returncode == 0
        except Exception as exc:
            p, ok = None, False
            print(f"[watchdog] 디스패치 실패: {type(exc).__name__}")
        doc["dispatch"] = {"at_kst": doc.get("checked_at_kst"), "ok": ok,
                           "cmd": " ".join(cmd),
                           "stderr_tail": ((p.stderr or "").strip().splitlines() or [""])[-1] if p else None}
        try:
            with open(exit_failsafe.OUT_PATH, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        print(f"[watchdog] 디스패치 {'성공' if ok else '실패'}")
        return 0 if ok else 1

    if "--check" in args:
        need = action_needed(doc)
        print(json.dumps({"action_required": need,
                          "overdue": doc.get("overdue"),
                          "cloud": doc.get("cloud")}, ensure_ascii=False))
        return 0 if need else 1

    # --record (기본)
    print(json.dumps({"checked_at_kst": doc.get("checked_at_kst"), "phase": doc.get("phase"),
                      "overdue": doc.get("overdue"), "cloud": doc.get("cloud"),
                      "action_required": action_needed(doc)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
