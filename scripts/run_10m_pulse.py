#!/usr/bin/env python3
"""
10분 단위 10인 직원 업무 업데이트 & 업무보고 파이프라인.

동훈님 지시(2026-11-17 12:02): "각 직원들이 10분 마다 각자 업무를 업데이트 해서
업무보고를 올릴 수 있도록 해줘"

한 번의 실행이 하는 일
----------------------
1. 실전 원장 동기화 (`scripts/sync_live_portfolio.py`, 캐시 기반 — 1초 내외)
2. 10인 에이전트 파이프라인 + 피드 생성 (`scripts/update_feed.py`, 약 5초)
   — 24시간 캐시된 백테스트는 다시 돌리지 않고 그대로 읽는다(Agent 7).
3. 각 직원이 **자기 담당 업무의 현재 상태 + 다음 행동**을 10분 보고로 1건씩 추가
   (무인 집행 현황·전략 자기점검 결과를 근거로 붙는다 — 수치를 만들지 않는다)
4. `cube.html`·`feed.html` 재렌더 + 위젯 번들 동기화 (약 1초)
5. `--push`면 `main`에 커밋·푸시 (동시 푸시 충돌 시 rebase 후 1회 재시도)

실행: python3 scripts/run_10m_pulse.py [--push] [--no-html]
종료 코드 0 = 성공. 소요 시간은 마지막 줄에 초 단위로 찍힌다(목표 10~20초).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import market_clock  # noqa: E402
from agents.cube_orchestrator import ROLE_DUTIES  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
FEED_PATH = os.path.join(DATA, "agent_feed.json")
DOCS_FEED_PATH = os.path.join(BASE_DIR, "docs", "agent_feed.json")
REPORT_PATH = os.path.join(DATA, "cube_report.json")
PULSE_PATH = os.path.join(DATA, "cube_pulse.json")
PULSE_LOG = os.path.join(DATA, "cube_pulse_log.jsonl")
AUTOTRADER_STATUS = os.path.join(DATA, "autotrader_status.json")
WIDGET_BUNDLE = os.path.abspath(os.path.join(BASE_DIR, "..", "widgets", "us-quant-dashboard", "bundle"))

NAMES = {
    1: "뉴스 & 센티먼트", 2: "기술적 변동성·모멘텀", 3: "실적 촉매", 4: "매크로·선행지표",
    5: "스마트머니·크레딧", 6: "퀀트 시나리오", 7: "백테스트 검증", 8: "알파 평가",
    9: "앙상블 융합", 10: "실전 집행",
}
ICONS = {1: "📰", 2: "📈", 3: "🏢", 4: "🌐", 5: "🐋", 6: "📐", 7: "🧪", 8: "🎯", 9: "🧬", 10: "⚡"}


def _load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _run(cmd, name, timeout=600):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=BASE_DIR, text=True, capture_output=True, timeout=timeout)
    dt = round(time.time() - t0, 1)
    ok = p.returncode == 0
    tail = ((p.stdout or "").strip().splitlines() or [""])[-1]
    print(f"  [{name}] {dt}s {'OK' if ok else 'FAILED'} — {tail[:120]}")
    if not ok:
        print("    " + "\n    ".join(((p.stderr or "").strip().splitlines() or [""])[-8:]))
    return ok, dt


# --------------------------------------------------------------- pulse content
def _num(v, digits=2, default="—"):
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return default


def _agent_state(aid, report, status, pulse_ctx):
    """직원별 '지금 무엇을 보고 있는가' — 실제 보고값에서만 만든다."""
    clocks = pulse_ctx["clock"]
    at = status or {}
    a = report or {}
    if aid == 1:
        sec = a.get("sector_sentiment") or {}
        return (f"24시간 뉴스 {a.get('data_sources', {}).get('items_last_24h')}건 · 종목 평균 센티 {a.get('average_sentiment_score')}점 "
                f"({a.get('overall_market_mood')}) · 채권 {sec.get('BONDS_FED', {}).get('sentiment_score')} / "
                f"외환 {sec.get('FX', {}).get('sentiment_score')} / 코인 {sec.get('CRYPTO', {}).get('sentiment_score')}",
                [{"label": "뉴스", "value": f"{a.get('data_sources', {}).get('items_last_24h')}건"},
                 {"label": "평균 센티", "value": f"{a.get('average_sentiment_score')}점", "highlight": True}])
    if aid == 2:
        lv = a.get("levels") or {}
        s = (lv.get("SOXL") or {})
        t = (lv.get("TQQQ") or {})
        return (f"SOXL ${_num(s.get('close'))} (ATR {s.get('atr_pct')}%, RSI2 {s.get('rsi2')}, %B {s.get('pct_b')}) · "
                f"TQQQ ${_num(t.get('close'))} (RSI2 {t.get('rsi2')}, %B {t.get('pct_b')}) → "
                f"진입 신호 {', '.join(a.get('actionable_symbols') or []) or '없음'}",
                [{"label": "SOXL ATR", "value": f"{s.get('atr_pct')}%", "highlight": True},
                 {"label": "SOXL RSI2", "value": s.get("rsi2")}])
    if aid == 3:
        er = (a.get("earnings_calendar") or [{}])
        nvda = next((e for e in er if e.get("ticker") == "NVDA"), {})
        return (f"NVDA 실적 D-{nvda.get('d_day')} ({nvda.get('report_kst')}) · 컨센 EPS {_num(nvda.get('eps_estimate'))} · "
                f"매크로 일정 {len(a.get('macro_calendar') or [])}건",
                [{"label": "NVDA", "value": f"D-{nvda.get('d_day')}", "highlight": True},
                 {"label": "발표", "value": nvda.get("report_kst", "—")}])
    if aid == 4:
        li = a.get("leading_indicators") or {}
        pr = li.get("recession_probit") or {}
        bm = (a.get("bond_market") or {}).get("yields_pct") or {}
        return (f"CUBE-LEI {li.get('cube_lei_index')} ({li.get('regime_ko')}) · 침체확률 {pr.get('probability_used')}% · "
                f"10Y {bm.get('10y')}% / 3M {bm.get('3m')}% · HY OAS {(li.get('credit') or {}).get('hy_oas_bp')}bp",
                [{"label": "CUBE-LEI", "value": f"{li.get('cube_lei_index')}", "highlight": True},
                 {"label": "침체확률", "value": f"{pr.get('probability_used')}%"}])
    if aid == 5:
        cf = a.get("credit_flow") or {}
        return (f"QQQ P/C {a.get('qqq_put_call_oi')} → {a.get('institutional_bias')} · HY OAS {cf.get('hy_oas_bp')}bp "
                f"({cf.get('credit_level')}) · 기관 듀레이션 {(a.get('duration_flow') or {}).get('long_duration_demand', '—')}",
                [{"label": "QQQ P/C", "value": a.get("qqq_put_call_oi"), "highlight": True},
                 {"label": "HY OAS", "value": f"{cf.get('hy_oas_bp')}bp"}])
    if aid == 6:
        lf = a.get("leading_indicator_filter") or {}
        return (f"국면 {lf.get('regime')} → 레버리지 계수 {lf.get('leverage_cap_factor')} · 종목 상한 {lf.get('position_cap_pct')}% · "
                f"유효 전략 {', '.join(a.get('approved_now') or []) or '없음'}",
                [{"label": "국면", "value": lf.get("regime", "—"), "highlight": True},
                 {"label": "종목 상한", "value": f"{lf.get('position_cap_pct')}%"}])
    if aid == 7:
        ev = (pulse_ctx.get("evolution") or {})
        stats = (ev.get("stats") or {})
        return (f"백테스트 캐시 유지(24h) · 전략점검 {ev.get('decision')} · 실전 표본 {stats.get('n', 0)}건 "
                f"(기대값 {stats.get('ev_pct', '—')}%/건, PF {stats.get('profit_factor', '—')}) — "
                f"표본 20건 미만이면 파라미터 교체를 보류합니다",
                [{"label": "표본", "value": f"{stats.get('n', 0)}건", "highlight": True},
                 {"label": "점검", "value": ev.get("decision", "—")}])
    if aid == 8:
        ap = a.get("profitable_scenarios") or []
        return (f"승인 시나리오 {len(ap)}개 · 폐기 {len(a.get('rejected_scenarios') or [])}개 · "
                f"SGOV 캐리 {(a.get('sgov_vs_cash') or {}).get('annual_yield_pct')}%/년 · "
                f"코인→월요일 갭 β {(a.get('crypto_weekend_gap_risk') or {}).get('beta_monday_gap_per_1pct_weekend_btc')}",
                [{"label": "승인", "value": f"{len(ap)}개", "highlight": True},
                 {"label": "SGOV", "value": f"{(a.get('sgov_vs_cash') or {}).get('annual_yield_pct')}%/년"}])
    if aid == 9:
        w = a.get("target_portfolio_weights") or {}
        return (f"태세 {a.get('market_stance')} (5축 복합 {a.get('composite_score'):+}) · "
                f"목표 주식 {sum(v for k, v in w.items() if k != 'SGOV') * 100:.0f}% / SGOV {w.get('SGOV', 0) * 100:.0f}%",
                [{"label": "태세", "value": a.get("market_stance"), "highlight": True},
                 {"label": "5축", "value": f"{a.get('composite_score'):+}"}])
    # 10 — 집행관: 무인 엔진 상태
    pos = at.get("positions") or {}
    entries = at.get("entries") or []
    exits = at.get("exits") or []
    policy = at.get("policy") or {}
    budget = (policy.get("entry_budget") or {})
    held = ", ".join(f"{k} {v.get('qty')}주" for k, v in pos.items()) or "없음"
    return (f"무인 집행 {at.get('mode', '대기')} · 보유 {len(pos)}종목 ({held}) · "
            f"이번 사이클 매도 {len(exits)} / 매수 {len(entries)} · 1회 예산 ${budget.get('budget_usd')} · "
            f"다음 게이트 {at.get('next_gate', '—')}",
            [{"label": "모드", "value": at.get("mode", "대기"), "highlight": True},
             {"label": "예산", "value": f"${budget.get('budget_usd')}"},
             {"label": "당일 실현", "value": f"${at.get('realized_pnl_today_usd', 0)}"}])


def pulse_posts(clock, report, status, evolution):
    ts = clock["now_kst"][:16].replace("-", "").replace(":", "").replace(" ", "")
    hhmm = clock["now_kst"][11:16]
    ctx = {"clock": clock, "evolution": evolution}
    by_id = {a.get("agent_id"): a.get("report") or {} for a in (report.get("agents") or [])}
    posts = []
    for aid in range(1, 11):
        report_agent = by_id.get(aid) or {}
        content, metrics = _agent_state(aid, report_agent, status, ctx)
        posts.append({
            "id": f"post-pulse-{aid:02d}-{ts}",
            "agent_id": aid,
            "agent_name": f"Agent {aid}: {NAMES[aid]}",
            "agent_role": "TEN_MIN_PULSE",
            "icon": ICONS[aid],
            "avatar_bg": "from-slate-600 to-slate-700",
            "badge_color": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
            "timestamp": clock["now_kst"][:16],
            "time_ago": "10분 보고",
            "category": "PULSE",
            "badge": f"⏱ {hhmm} 10분 업무보고",
            "title": f"[{hhmm} 10분 업무보고] {NAMES[aid]} — {ROLE_DUTIES.get(aid, '')}"[:220],
            "content": content,
            "metrics": metrics,
            "action_required": _next_action(aid, status),
            "reactions": {},
            "pinned": False,
            "phase": clock.get("phase"),
        })
    return posts


def _next_action(aid, status):
    at = status or {}
    if aid == 10:
        return (f"다음 10분 사이클에서 {'청산·' if at.get('positions') else ''}진입 게이트 재평가 "
                f"({at.get('next_gate') or '세션 대기'}). 주문은 정규장에서만 나갑니다.")
    if aid == 7:
        return "표본 20건 전까지는 방어 모드·변동성 계수만 적용하고 파라미터 교체는 보류합니다."
    if aid == 4:
        return "VIX 24 이상 + 확인 2개가 되면 숏 헤지 게이트가 열립니다(현재 조건 미충족 시 대기)."
    if aid == 3:
        return "NVDA 실적(11-18 06:20 KST) 전 신규 레버리지 노출 금지 · 05:40 강제청산 유지."
    return "다음 10분 주기에 실측값을 갱신해 재보고합니다."


def autotrader_summary(status):
    if not status:
        return ("무인 집행 엔진 상태 파일 없음 — 첫 사이클 대기", [])
    policy = status.get("policy") or {}
    g = policy.get("guardrails") or {}
    eb = policy.get("entry_budget") or {}
    metrics = [
        {"label": "모드", "value": status.get("mode"), "highlight": True},
        {"label": "1회 예산", "value": f"${eb.get('budget_usd')}"},
        {"label": "일일 손실한도", "value": f"-${status.get('loss_limit_usd')}"},
        {"label": "당일 실현", "value": f"${status.get('realized_pnl_today_usd')}"},
    ]
    lines = [
        f"집행 채널 GitHub Actions(로컬 8443 차단) · 정책 enabled={policy.get('enabled')} · ask_before_trade={policy.get('ask_before_trade')}",
        f"가드레일: 1회 예산 ≤ 현금 {g.get('entry_budget_pct_of_cash'):g}% / 자산 {g.get('entry_budget_pct_of_equity'):g}% "
        f"/ 절대 ${g.get('entry_budget_cap_usd'):g} · 보유 {g.get('max_open_positions'):g}종목 · 세션 신규 {g.get('max_entries_per_session'):g}건",
        f"손실 서킷브레이커: 당일 실현 -${status.get('loss_limit_usd')} 도달 시 신규 매수 중단(청산은 계속) · "
        f"현재 halted={status.get('halted')}",
        f"개장 유예 {g.get('opening_blackout_kst')} · 신규 진입 마감 {g.get('no_new_entry_after_kst')} · "
        f"강제청산 {g.get('force_exit_kst')} · VIX 계수 {policy.get('vix_scale')}",
        f"이번 사이클: 매도 {len(status.get('exits') or [])}건 / 매수 {len(status.get('entries') or [])}건 · "
        f"대기주문 {len(status.get('pending_orders') or [])}건 · 다음 게이트 {status.get('next_gate')}",
    ]
    return ("\n".join(lines), metrics)


def build_pulse(clock, report, status, evolution, elapsed_stages):
    posts = pulse_posts(clock, report, status, evolution)
    text, metrics = autotrader_summary(status)
    hhmm = clock["now_kst"][11:16]
    ts = clock["now_kst"][:16].replace("-", "").replace(":", "").replace(" ", "")
    posts.append({
        "id": f"post-pulse-autotrader-{ts}",
        "agent_id": 10,
        "agent_name": "🤖 무인 자동매매 집행 엔진",
        "agent_role": "AUTONOMOUS_INTRADER",
        "icon": "🤖",
        "avatar_bg": "from-emerald-600 to-teal-700",
        "badge_color": "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
        "timestamp": clock["now_kst"][:16],
        "time_ago": "10분 보고",
        "category": "AUTOTRADER",
        "badge": f"🤖 {hhmm} 자율집행 현황",
        "title": f"[{hhmm}] 무인 자동매매 — {status.get('mode', '대기')} · 매도 {len(status.get('exits') or [])} / 매수 {len(status.get('entries') or [])}",
        "content": text,
        "metrics": metrics,
        "action_required": "손절·강제청산은 확인 없이 즉시, 신규 매수는 게이트 통과 시에만 집행됩니다.",
        "reactions": {},
        "pinned": True,
        "phase": clock.get("phase"),
    })
    return posts


def push_to_main(message):
    def git(*args):
        return subprocess.run(["git", *args], cwd=BASE_DIR, text=True, capture_output=True)
    for attempt in (1, 2):
        r = git("push", "origin", "main")
        if r.returncode == 0:
            return True
        print(f"  push 실패({attempt}/2): {(r.stderr or '').strip().splitlines()[-1:] }")
        git("pull", "--rebase", "--autostash", "origin", "main")
    return False


def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:])
    do_push = "--push" in args
    build_html = "--no-html" not in args
    t0 = time.time()
    stages = {}
    clock = market_clock.resolve()
    print(f"=== 10분 업무보고 파이프라인 {clock['now_kst']} KST phase={clock['phase']}")

    ok, dt = _run([sys.executable, "scripts/sync_live_portfolio.py"], "1/4 원장 동기화")
    stages["ledger_sync"] = dt
    ok2, dt = _run([sys.executable, "scripts/update_feed.py"], "2/4 10인 에이전트 업무보고")
    stages["agents"] = dt
    if not ok2:
        print("RESULT: FAILED (agent pipeline)")
        return 1

    report = _load(REPORT_PATH, {}) or {}
    status = _load(AUTOTRADER_STATUS, {}) or {}
    evolution = status.get("evolution") or {}
    if not evolution:
        ev_log = _load(os.path.join(DATA, "strategy_evolution_log.json"), {}) or {}
        evolution = (ev_log.get("entries") or [{}])[-1]

    feed = _load(FEED_PATH, {}) or {}
    posts = [p for p in (feed.get("posts") or []) if not str(p.get("id", "")).startswith("post-pulse-")]
    new_posts = build_pulse(clock, report, status, evolution, stages)
    feed["posts"] = new_posts + posts
    feed["total_posts"] = len(feed["posts"])
    feed["pulse"] = {
        "cycle_kst": clock["now_kst"][:16],
        "interval_min": 10,
        "employees_updated": 10,
        "stage_seconds": stages,
        "autotrader_mode": status.get("mode"),
        "next_gate": status.get("next_gate"),
    }
    feed["last_updated"] = clock["now_kst"] + " KST"
    _save(FEED_PATH, feed)
    _save(DOCS_FEED_PATH, feed)

    if build_html:
        ok3, dt = _run([sys.executable, "scripts/build_full_cube.py"], "3/4 대시보드 렌더")
        stages["render"] = dt
        if os.path.isdir(WIDGET_BUNDLE):
            shutil.copyfile(os.path.join(BASE_DIR, "cube.html"), os.path.join(WIDGET_BUNDLE, "index.html"))
            print(f"  [위젯] {os.path.join(WIDGET_BUNDLE, 'index.html')}")
    else:
        ok3 = True

    pulse = {
        "generated_at_kst": clock["now_kst"],
        "phase": clock["phase"],
        "employees": [
            {"agent_id": aid, "name": NAMES[aid], "duty": ROLE_DUTIES.get(aid),
             "post_id": f"post-pulse-{aid:02d}"}
            for aid in range(1, 11)
        ],
        "autotrader": {k: status.get(k) for k in ("mode", "phase", "next_gate", "realized_pnl_today_usd",
                                                   "halted", "entries", "exits", "pending_orders")},
        "evolution": {"decision": evolution.get("decision"), "decisions": evolution.get("decisions")},
        "stage_seconds": stages,
        "total_posts": len(feed["posts"]),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    # 렌더까지 끝난 뒤 단계 시간을 최종값으로 다시 기록한다(대시보드가 읽는 feed).
    feed.setdefault("pulse", {})["stage_seconds"] = stages
    feed["pulse"]["elapsed_sec"] = pulse["elapsed_sec"]
    _save(FEED_PATH, feed)
    _save(DOCS_FEED_PATH, feed)
    _save(PULSE_PATH, pulse)
    with open(PULSE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_kst": clock["now_kst"], "elapsed_sec": pulse["elapsed_sec"],
                            "stages": stages, "posts": len(feed["posts"])}, ensure_ascii=False) + "\n")

    pushed = False
    if do_push:
        git = lambda *a: subprocess.run(["git", *a], cwd=BASE_DIR, text=True, capture_output=True)  # noqa: E731
        git("add", "-A")
        msg = f"chore(cube): 10분 업무보고 {clock['now_kst'][11:16]} KST ({len(feed['posts'])} posts)"
        c = git("commit", "-m", msg)
        if c.returncode == 0:
            pushed = push_to_main(msg)
            print(f"  [push] {'OK' if pushed else 'FAILED'}")
        else:
            print("  [push] 변경 없음")

    total = round(time.time() - t0, 1)
    print(f"RESULT: OK — 직원 10명 보고 + 자율집행 현황 · posts {len(feed['posts'])} · "
          f"stages {stages} · total {total}s" + (f" · pushed={pushed}" if do_push else ""))
    return 0 if (ok and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(main())
