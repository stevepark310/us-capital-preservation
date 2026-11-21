#!/usr/bin/env python3
"""
무인 자동매매 CLI — 10분 사이클 진입점.

    python3 scripts/autotrader.py --execute            # 실주문 (NH는 GitHub Actions 경유)
    python3 scripts/autotrader.py --execute --push     # 실주문 + 상태·브래킷 main 반영
    python3 scripts/autotrader.py --dry-run            # 판단 미리보기 (주문·상태 변경 없음)
    python3 scripts/autotrader.py --status             # 마지막 사이클 상태 출력

종료 코드
  0 정상 (주문 없음 포함)
  2 정책 비활성/리스 미확보로 아무것도 하지 않음
  1 실행 오류

`--execute`는 사용자가 위임한 무인 집행 경로다(config/swing_approval.json →
autonomous_execution.delegated_at_kst 2026-11-17 12:02). 손절·강제청산은
가드레일과 무관하게 집행되고, 신규 매수만 예산·손실한도·쿨다운 게이트를 받는다.
"""

import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import execution_policy  # noqa: E402
from core.autotrader import AutonomousTrader, STATUS_PATH, refresh_status  # noqa: E402

COMMIT_PATHS = [
    "data/active_brackets.json", "data/autotrader_state.json", "data/autotrader_status.json",
    "data/autotrader_log.jsonl", "data/strategy_trade_ledger.json", "data/strategy_params.json",
    "data/strategy_evolution_log.json", "data/live_order_responses.jsonl",
    "data/order_dispatch_state.json", "data/nh_account_snapshot.json",
]


def push_state(message: str) -> bool:
    """집행 상태·브래킷을 main에 반영(충돌 시 rebase 후 1회 재시도)."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=BASE_DIR, text=True, capture_output=True)
    git("add", *COMMIT_PATHS)
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("  [push] 변경 없음")
        return True
    c = git("commit", "-m", message)
    if c.returncode != 0:
        print(f"  [push] commit 실패: {(c.stderr or '').strip()[-200:]}")
        return False
    for _ in range(2):
        r = git("push", "origin", "main")
        if r.returncode == 0:
            print("  [push] OK")
            return True
        git("pull", "--rebase", "--autostash", "origin", "main")
    print("  [push] 실패")
    return False


def _print_status(st: dict) -> None:
    policy = st.get("policy") or {}
    print(f"=== AUTONOMOUS_INTRADER [{st.get('mode')}] {st.get('generated_at_kst')} "
          f"phase={st.get('phase')} session={st.get('session_date')}")
    led = st.get("ledger") or {}
    print(f"  계좌 평가 ${led.get('equity_usd')} / 현금 ${led.get('cash_usd')} · "
          f"당일 실현 ${st.get('realized_pnl_today_usd')} (한도 -${st.get('loss_limit_usd')}) · "
          f"halted={st.get('halted')}")
    eb = (policy.get("entry_budget") or {})
    print(f"  1회 진입 예산 ${eb.get('budget_usd')} (binding={eb.get('binding')}, "
          f"VIX {policy.get('vix')} → 계수 {policy.get('vix_scale')} × 변동성 계수 "
          f"{policy.get('strategy_risk_scale')} = {policy.get('budget_scale_total')}) · "
          f"진입 {st.get('entries_today')}건 / 주문 {st.get('orders_today')}건")
    for p in st.get("pending_orders") or []:
        print(f"  대기주문 {p.get('side')} {p.get('ticker')} {p.get('qty')}주 @ ${p.get('limit_price')} "
              f"(orr {p.get('order_no')})")
    for tk, pos in (st.get("positions") or {}).items():
        print(f"  보유 {tk} {pos.get('qty')}주 @ ${pos.get('entry')} · SL ${pos.get('sl')} · {pos.get('exit_mode')}")
    for a in st.get("exits") or []:
        print(f"  ▶ 매도 {a.get('ticker')} {a.get('qty')}주 @ ${a.get('price')} — {a.get('reason')} [{a.get('status')}]")
    for a in st.get("entries") or []:
        print(f"  ▶ 매수 {a.get('ticker')} {a.get('qty')}주 @ ${a.get('price')} — {a.get('source')} [{a.get('status')}]")
    ev = st.get("evolution") or {}
    print(f"  전략점검 {ev.get('decision')}: {'; '.join(ev.get('decisions') or []) or ev.get('reason') or '변경 없음'}")
    print(f"  다음 게이트: {st.get('next_gate')} · {st.get('elapsed_sec')}s")


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    policy = execution_policy.load_policy()

    if "--status" in args:
        try:
            with open(STATUS_PATH, "r", encoding="utf-8") as f:
                _print_status(json.load(f))
            return 0
        except Exception as e:
            print(f"상태 파일 없음: {e}")
            return 1

    if "--refresh-status" in args:
        # 집행 판단 없이 상태 파일만 '지금' 기준으로 다시 만든다(10분 펄스·클라우드 사이클 공용).
        st = refresh_status(by=("cloud" if os.getenv("GITHUB_ACTIONS") == "true" else "pulse"))
        print(f"✅ autotrader_status.json 최신화 {st.get('generated_at_kst')} · "
              f"phase={st.get('phase')} · session={st.get('session_date')} · "
              f"보유 {len(st.get('positions') or {})}종목 · 예산 ${(st.get('policy') or {}).get('entry_budget', {}).get('budget_usd')}")
        return 0

    execute = "--execute" in args
    # The CLI itself must not bypass the cloud-level approval gate. 게이트는 dry-run
    # 미리보기에도 읽어서 '지금 이 세션이 매도 전용인지'를 판단에 반영한다(주문은 없다).
    gate_path = os.path.join(BASE_DIR, "config", "live_execution_gate.json")
    try:
        with open(gate_path, "r", encoding="utf-8") as handle:
            live_gate = json.load(handle)
    except (OSError, ValueError):
        live_gate = {}
    from core import market_clock
    from core.live_session_gate import check_live_gate
    authorization = check_live_gate(live_gate, market_clock.resolve())
    if execute and not authorization["ok"]:
        print(f"[autotrader] BLOCKED: {authorization['reason']}")
        return 2
    if authorization.get("sell_only"):
        print("[autotrader] 매도 전용 게이트 — 신규 매수 0건, 청산만 집행합니다")
    if not execute and "--dry-run" not in args:
        print("사용법: python3 scripts/autotrader.py --execute | --dry-run | --status")
        return 2

    if not policy.get("enabled"):
        print("[autotrader] 정책 비활성(config/swing_approval.json autonomous_execution.enabled=false)")
        return 2

    trader = AutonomousTrader(policy=policy, dry_run=not execute,
                              sell_only=bool(authorization.get("sell_only")))
    if not trader.acquire_lease("autotrader"):
        owner = AutonomousTrader.lease_owner_fresh(
            float(((policy.get("safety") or {}).get("single_writer_lease_min") or 15)))
        print(f"[autotrader] 다른 집행 엔진이 실행 중입니다 (lease owner={owner}). 이번 사이클을 건너뜁니다.")
        return 2
    try:
        status = trader.run(execute=execute)
        _print_status(status)
        if "--push" in args:
            push_state(f"chore(autotrader): {status.get('mode')} cycle {status.get('session_date')} "
                       f"매도 {len(status.get('exits') or [])} / 매수 {len(status.get('entries') or [])}")
        if "--json" in args:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        print(f"[autotrader] 실행 오류: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        trader.release_lease("autotrader")


if __name__ == "__main__":
    sys.exit(main())
