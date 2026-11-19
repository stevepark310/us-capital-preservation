#!/usr/bin/env python3
"""
CUBE 협업 사이클 — 10인이 서로 업무 결과를 주고받고 빌드업하는 한 주기.

한 번의 실행이 하는 일
----------------------
1. 실측 사실(facts) 수집 — 10인 보고서·원장·집행 상태·클라우드 상태에서 숫자만 뽑는다.
2. 직원별 업무 현황을 버스(`agent_state`)에 갱신하고, 3사이클마다 업무보고 메시지를 올린다.
3. 리서치 데스크(1~5)가 **인사이트 → 가설**을 검증 데스크(6~8)로 핸드오프한다.
4. 검증 데스크가 그 가설을 `core/desk_lab`으로 실제 측정(In/Out-of-Sample)하고,
   게이트를 통과한 것만 승인(APPROVAL) 또는 보류(HOLD)로 돌려준다.
5. 총괄(9)·집행(10)이 승인분을 반영하고(자동 적용 범위는 진입 필터 조이기뿐),
   체결·브래킷을 복기해 다음 사이클의 피드백을 남긴다.
6. 결과를 `data/agent_collab_bus.json`, `data/strategy_buildup.json`,
   `data/collab_cycle.json`에 영속화한다.

원칙
----
* 숫자를 만들지 않는다 — 모든 문장은 파일에서 읽은 값으로만 만든다.
* 게이트·하드락을 통과하지 못한 후보는 적용하지 않는다(`core/buildup`).
* 네트워크 없이 5초 안에 끝난다(일봉 캐시 사용). 데이터가 그대로면 재측정하지 않는다.

실행: python3 scripts/run_collab_cycle.py [--json] [--every N]
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from agents import agent_profiles  # noqa: E402
from core import buildup, collab_bus, desk_lab, market_clock, strategy_evolver  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
CYCLE_PATH = os.path.join(DATA, "collab_cycle.json")
DUTY_EVERY = 1          # 협업 사이클마다 10인 업무보고 메시지


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _num(v, digits: int = 2, default: str = "—") -> str:
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return default


# --------------------------------------------------------------------- facts
def collect_facts() -> Dict[str, Any]:
    rep = _load(os.path.join(DATA, "cube_report.json"), {}) or {}
    status = _load(os.path.join(DATA, "autotrader_status.json"), {}) or {}
    port = _load(os.path.join(DATA, "live_portfolio.json"), {}) or {}
    plan = _load(os.path.join(DATA, "avg_anchor_plan.json"), {}) or {}
    brackets = _load(os.path.join(DATA, "active_brackets.json"), {}) or {}
    cloud = _load(os.path.join(DATA, "cloud_status.json"), {}) or {}
    rec = _load(os.path.join(DATA, "position_reconciliation.json"), {}) or {}
    ledger = _load(os.path.join(DATA, "strategy_trade_ledger.json"), {}) or {}
    by_id = {a.get("agent_id"): (a.get("report") or {}) for a in (rep.get("agents") or [])}
    clock = market_clock.resolve()

    a1 = by_id.get(1) or {}
    a2 = by_id.get(2) or {}
    a3 = by_id.get(3) or {}
    a4 = by_id.get(4) or {}
    a5 = by_id.get(5) or {}
    a6 = by_id.get(6) or {}
    a8 = by_id.get(8) or {}
    a9 = by_id.get(9) or {}
    levels = a2.get("levels") or {}
    soxl = levels.get("SOXL") or {}
    tqqq = levels.get("TQQQ") or {}
    policy = status.get("policy") or {}
    eb = policy.get("entry_budget") or {}
    g = policy.get("guardrails") or {}
    params = strategy_evolver.load_params()
    trades = ledger.get("trades") or []

    positions = {p.get("ticker"): p for p in (port.get("positions") or []) if isinstance(p, dict)}
    open_brackets = {k: v for k, v in brackets.items()
                     if isinstance(v, dict) and v.get("entry_price") and not v.get("closed")
                     and not v.get("carry_parking")}
    return {
        "clock": clock,
        "phase": clock.get("phase"),
        "session_date": clock.get("session_date"),
        "kst_shift_min": clock.get("kst_shift_min"),
        "sentiment": {"avg": a1.get("average_sentiment_score"), "mood": a1.get("overall_market_mood"),
                      "news_24h": (a1.get("data_sources") or {}).get("items_last_24h"),
                      "sectors": a1.get("sector_sentiment") or {}},
        "tech": {"SOXL": soxl, "TQQQ": tqqq, "signals": a2.get("actionable_symbols") or []},
        "catalyst": {"earnings": (a3.get("earnings_calendar") or [])[:2],
                     "macro_events": len(a3.get("macro_calendar") or [])},
        "macro": {"lei": (a4.get("leading_indicators") or {}), "vix": a4.get("vix_level"),
                  "regime": a4.get("macro_regime")},
        "positioning": {"pc": a5.get("qqq_put_call_oi"), "bias": a5.get("institutional_bias"),
                        "credit": a5.get("credit_flow") or {}},
        "quant": {"regime": (a6.get("leading_indicator_filter") or {}).get("regime"),
                  "leverage": (a6.get("leading_indicator_filter") or {}).get("leverage_cap_factor"),
                  "approved_now": a6.get("approved_now") or []},
        "alpha": {"approved": len(a8.get("profitable_scenarios") or []),
                  "rejected": len(a8.get("rejected_scenarios") or []),
                  "sgov_yield": (a8.get("sgov_vs_cash") or {}).get("annual_yield_pct")},
        "ensemble": {"stance": a9.get("market_stance"), "composite": a9.get("composite_score"),
                     "weights": a9.get("target_portfolio_weights") or {}},
        "execution": {"mode": status.get("mode"), "phase": status.get("phase"),
                      "positions": status.get("positions") or {},
                      "pending": status.get("pending_orders") or [],
                      "budget": eb.get("budget_usd"), "budget_binding": eb.get("binding"),
                      "realized_today": status.get("realized_pnl_today_usd"),
                      "halted": status.get("halted"), "next_gate": status.get("next_gate"),
                      "generated_at": status.get("generated_at_kst"),
                      "status_sync": status.get("status_sync") or {}},
        "portfolio": {"equity": port.get("current_total_equity_usd"), "cash": port.get("cash_buffer_usd"),
                      "realized": port.get("realized_pnl_usd"), "unrealized": port.get("unrealized_pnl_usd"),
                      "win_rate": port.get("win_rate_pct"), "trades": port.get("trades_summary"),
                      "positions": {k: {"qty": v.get("qty"), "avg": v.get("avg_price"),
                                        "mark": v.get("market_price"), "unreal": v.get("unrealized_usd")}
                                    for k, v in positions.items()}},
        "brackets": {k: {"entry": v.get("entry_price"), "sl": v.get("sl"), "tp": v.get("tp1") or v.get("tp"),
                         "qty": v.get("bought_qty"), "exit_mode": v.get("exit_mode") or v.get("bracket_version"),
                         "force_exit_kst": v.get("force_exit_kst"),
                         "trail_stop": v.get("trail_stop"), "locked": bool(v.get("levels_locked"))}
                     for k, v in open_brackets.items()},
        "ladders": [{"ticker": l.get("ticker"), "qty": l.get("qty"), "avg": l.get("avg_price"),
                     "tp1": l.get("tp1"), "sl": l.get("sl")} for l in (plan.get("ladders") or [])],
        "ledger": {"trades": len(trades),
                   "ev_pct": (sum(float(t.get("pnl_pct") or 0) for t in trades) / len(trades)) if trades else None,
                   "last": trades[-1] if trades else None},
        "params": params,
        "cloud": cloud,
        "reconciliation": {"status": rec.get("status"), "ghosts": rec.get("ghost_count"),
                           "checked_at": rec.get("checked_at_kst")},
        "guardrails": g,
    }


# --------------------------------------------------------------------- duty lines
def duty_line(aid: int, f: Dict[str, Any]) -> Dict[str, Any]:
    """직원별 '지금 무엇을 보고 있는가' — 파일에서 읽은 값만 쓴다."""
    def ok(v):
        return v not in (None, "", [], {})

    if aid == 1:
        s = f["sentiment"]
        line = (f"뉴스 {s['news_24h']}건 · 평균 센티 {s['avg']}점({s['mood']}) · "
                f"채권 {(s['sectors'].get('BONDS_FED') or {}).get('sentiment_score')} / "
                f"외환 {(s['sectors'].get('FX') or {}).get('sentiment_score')} / "
                f"코인 {(s['sectors'].get('CRYPTO') or {}).get('sentiment_score')}")
        return {"line": line, "metrics": {"평균 센티": s["avg"], "24h 뉴스": s["news_24h"]},
                "to": collab_bus.DESK_VALIDATION}
    if aid == 2:
        sx, tq = f["tech"]["SOXL"], f["tech"]["TQQQ"]
        line = (f"SOXL ${_num(sx.get('close'))} ATR {sx.get('atr_pct')}% RSI2 {sx.get('rsi2')} "
                f"%B {sx.get('pct_b')} RVOL {sx.get('rvol')} · TQQQ ATR {tq.get('atr_pct')}% "
                f"· 진입 신호 {', '.join(f['tech']['signals']) or '없음'}")
        return {"line": line, "metrics": {"SOXL ATR%": sx.get("atr_pct"), "SOXL RVOL": sx.get("rvol")},
                "to": collab_bus.DESK_VALIDATION}
    if aid == 3:
        e = (f["catalyst"]["earnings"] or [{}])[0]
        line = (f"실적 D-{e.get('d_day')} {e.get('ticker') or '—'} ({e.get('report_kst')}) · "
                f"매크로 일정 {f['catalyst']['macro_events']}건")
        return {"line": line, "metrics": {"실적": e.get("ticker"), "D-day": e.get("d_day")},
                "to": collab_bus.DESK_VALIDATION}
    if aid == 4:
        m = f["macro"]
        li = m["lei"] or {}
        pr = li.get("recession_probit") or {}
        line = (f"CUBE-LEI {li.get('cube_lei_index')} ({li.get('regime_ko')}) · "
                f"침체확률 {pr.get('probability_used')}% · VIX {m.get('vix')} · 국면 {m.get('regime')}")
        return {"line": line, "metrics": {"CUBE-LEI": li.get("cube_lei_index"), "VIX": m.get("vix")},
                "to": collab_bus.DESK_VALIDATION}
    if aid == 5:
        p = f["positioning"]
        line = (f"QQQ P/C {p.get('pc')} → {p.get('bias')} · HY OAS {(p.get('credit') or {}).get('hy_oas_bp')}bp "
                f"({(p.get('credit') or {}).get('credit_level')})")
        return {"line": line, "metrics": {"QQQ P/C": p.get("pc"), "HY OAS": (p.get("credit") or {}).get("hy_oas_bp")},
                "to": collab_bus.DESK_VALIDATION}
    if aid == 6:
        q = f["quant"]
        line = (f"국면 {q['regime']} → 레버리지 계수 {q['leverage']} · 유효 전략 "
                f"{', '.join(q['approved_now']) or '없음'}")
        return {"line": line, "metrics": {"국면": q["regime"], "레버리지": q["leverage"]},
                "to": collab_bus.DESK_EXECUTIVE}
    if aid == 7:
        lg = f["ledger"]
        line = (f"실전 표본 {lg['trades']}건 · 건당 기대값 {_num(lg['ev_pct'], 3)}% · "
                f"캐시 기준일 {desk_lab.data_as_of(desk_lab.load_daily()) or '—'}")
        return {"line": line, "metrics": {"표본": lg["trades"], "기대값%": _num(lg["ev_pct"], 3)},
                "to": collab_bus.DESK_EXECUTIVE}
    if aid == 8:
        a = f["alpha"]
        line = (f"승인 시나리오 {a['approved']} · 폐기 {a['rejected']} · SGOV {a['sgov_yield']}%/년")
        return {"line": line, "metrics": {"승인": a["approved"], "SGOV": a["sgov_yield"]},
                "to": collab_bus.DESK_EXECUTIVE}
    if aid == 9:
        e = f["ensemble"]
        line = (f"태세 {e['stance']} (5축 {e['composite']:+}) · 시장 국면 {f['macro']['regime']}"
                if e.get("composite") is not None else f"태세 {e['stance']}")
        return {"line": line, "metrics": {"태세": e["stance"], "5축": e["composite"]},
                "to": collab_bus.DESK_EXECUTIVE}
    x = f["execution"]
    held = ", ".join(f"{k} {v.get('qty')}주" for k, v in (x["positions"] or {}).items()) or "없음"
    line = (f"집행 {x['mode']} · 보유 {held} · 당일 실현 ${x['realized_today']} · 1회 예산 ${x['budget']} "
            f"· 대기주문 {len(x['pending'])} · {x['next_gate']}")
    return {"line": line, "metrics": {"모드": x["mode"], "예산": x["budget"], "보유": len(x["positions"] or {})},
            "to": []}


def insight(aid: int, f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """리서치 데스크의 인사이트 1건(검증 데스크에 넘길 질문 포함)."""
    if aid == 1:
        s = f["sentiment"]
        mood = float(s["avg"]) if isinstance(s["avg"], (int, float)) else 50.0
        return {"subject": f"센티먼트 {s['avg']}점 — 진입 필터를 조일 근거인가",
                "body": (f"24시간 뉴스 {s['news_24h']}건의 평균 감성은 {s['avg']}점({s['mood']})입니다. "
                         f"감성이 낮은 구간에서 눌림 진입 필터를 더 조이면 기대값이 개선되는지 검증해 주세요."),
                "ask": "entry_filters 조이기 후보의 OOS 기대값 비교",
                "metrics": {"평균 센티": s["avg"], "기준": 50}}
    if aid == 2:
        sx = f["tech"]["SOXL"]
        return {"subject": f"SOXL ATR {sx.get('atr_pct')}% — 손절·익절 폭이 변동성에 맞는가",
                "body": (f"SOXL 일봉 ATR {sx.get('atr_pct')}%, RSI2 {sx.get('rsi2')}, %B {sx.get('pct_b')}, "
                         f"RVOL {sx.get('rvol')}. 현 규격(ATR×1.5 대칭·3세션)이 이 변동성에서 최적인지, "
                         f"손절을 1.2×ATR/1.8×ATR로 바꾼 경우와 비교해 주세요."),
                "ask": "일봉 스윙 손절 배수 A/B", "metrics": {"ATR%": sx.get("atr_pct"), "RVOL": sx.get("rvol")}}
    if aid == 3:
        e = (f["catalyst"]["earnings"] or [{}])[0]
        return {"subject": f"실적 캘린더 — 반응일 블랙아웃이 실제로 손실을 줄였는가",
                "body": (f"다음 실적은 {e.get('ticker') or '—'} {e.get('report_kst')}입니다. "
                         f"실적 반응일 C5 진입 제외가 손실 회피로 이어졌는지 실전 표본으로 확인해 주세요."),
                "ask": "블랙아웃 규칙의 실전 기여도", "metrics": {"실적": e.get("ticker")}}
    if aid == 4:
        li = f["macro"]["lei"] or {}
        pr = li.get("recession_probit") or {}
        return {"subject": f"CUBE-LEI {li.get('cube_lei_index')} — 리스크 계수 축소가 필요한가",
                "body": (f"CUBE-LEI {li.get('cube_lei_index')}({li.get('regime_ko')}), 침체확률 "
                         f"{pr.get('probability_used')}%, VIX {f['macro']['vix']}. 레버리지 계수와 "
                         f"1회 예산을 더 줄여야 하는 국면인지 판단해 주세요."),
                "ask": "국면별 예산·계수 조정", "metrics": {"LEI": li.get("cube_lei_index"), "VIX": f["macro"]["vix"]}}
    if aid == 5:
        p = f["positioning"]
        return {"subject": f"기관 포지셔닝 P/C {p.get('pc')} — 반도체 수급 리스크",
                "body": (f"QQQ 풋/콜 {p.get('pc')}({p.get('bias')}), HY OAS "
                         f"{(p.get('credit') or {}).get('hy_oas_bp')}bp. 반도체 노출을 줄일 근거가 되는지 확인해 주세요."),
                "ask": "신용·수급 기반 노출 축소", "metrics": {"P/C": p.get("pc")}}
    return None


def _recent_message(bus: Dict[str, Any], subject: str, hours: float = 6.0) -> bool:
    """같은 제목의 메시지가 최근 hours 안에 있으면 True(반복 공지 방지)."""
    from datetime import datetime as _dt
    now = _dt.strptime(collab_bus._now_kst(), "%Y-%m-%d %H:%M:%S")
    for m in reversed(bus.get("messages") or []):
        if m.get("subject") != subject:
            continue
        try:
            t = _dt.strptime(str(m.get("ts_kst"))[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False
        return (now - t).total_seconds() < hours * 3600
    return False


# --------------------------------------------------------------------- cycle
def run_cycle(every: int = DUTY_EVERY) -> Dict[str, Any]:
    bus = collab_bus.load()
    doc = buildup.load()
    f = collect_facts()
    clock = f["clock"]
    bus["cycle"] = int(bus.get("cycle") or 0) + 1
    cycle = bus["cycle"]
    cycle_no = cycle
    posted: List[str] = []

    def name(aid: int) -> str:
        return agent_profiles.SHORT_NAMES.get(aid, f"Agent {aid}")

    # 1) 직원별 현황 갱신 + (주기) 업무보고
    state = bus.setdefault("agent_state", {})
    for aid in range(1, 11):
        d = duty_line(aid, f)
        prev = state.get(str(aid)) or {}
        state[str(aid)] = {"ts_kst": collab_bus._now_kst(), "line": d["line"],
                           "metrics": d["metrics"], "phase": f["phase"]}
        if cycle_no % max(1, int(every)) == 0 or not prev.get("line"):
            if prev.get("line") != d["line"]:
                collab_bus.post(bus, aid, name(aid), "DUTY_REPORT",
                                f"{collab_bus.DESK_KO.get(collab_bus.DESKS[aid])} 업무 현황",
                                d["line"], to=d.get("to") or [], metrics=d["metrics"],
                                phase=f["phase"])
                posted.append(f"DUTY-{aid}")

    # 2) 리서치 데스크 인사이트 → 검증 데스크 핸드오프
    handoffs = []
    for aid in collab_bus.DESK_RESEARCH:
        ins = insight(aid, f)
        if not ins:
            continue
        cid = collab_bus.thread_id(bus, f"INS{aid}")
        collab_bus.post(bus, aid, name(aid), "INSIGHT", ins["subject"], ins["body"],
                        to=collab_bus.DESK_VALIDATION, metrics=ins["metrics"],
                        correlation_id=cid, refs={"ask": ins["ask"]}, phase=f["phase"])
        handoffs.append({"from": aid, "correlation_id": cid, "ask": ins["ask"], "subject": ins["subject"]})

    # 3) 가설: 대기 중이면 측정, 데이터가 새로우면 새 가설 발행
    daily = desk_lab.load_daily()
    fp = desk_lab.data_fingerprint(daily)
    as_of = desk_lab.data_as_of(daily)
    if not daily:
        # 가격 캐시가 없으면 측정할 수 없다 — 빈 데이터로 가설을 만들지 않는다.
        subject = "검증 데이터 없음 — 가격 캐시 갱신 대기"
        if not _recent_message(bus, subject, hours=6):
            collab_bus.post(bus, 7, name(7), "FEEDBACK", subject,
                            ("일봉 캐시(data_cache/backtest_daily.pkl)가 없어 In/Out-of-Sample 측정을 시작하지 않습니다. "
                             "`scripts/refresh_price_cache.py`가 캐시를 채운 뒤 같은 후보를 다시 잽니다."),
                            to=[6, 8, 9], metrics={"캐시": "없음"}, phase=f["phase"])
        daily = {}
    pending = buildup.pending_measurement(doc) if daily else []
    hypothesis = None
    measured_now = False
    if pending:
        hypothesis = pending[0]
        hypothesis["status"] = "MEASURED"
    else:
        grid = buildup.candidate_grid(f["params"])
        last_titles = {h.get("title") for h in (doc.get("hypotheses") or []) if h.get("data_fingerprint") == fp}
        fresh = [g for g in grid if g["title"] not in last_titles] if daily else []
        if fresh:
            item = fresh[0]
            hypothesis = buildup.record_hypothesis(doc, item, owner=2 if item["axis"] == "ENTRY_FILTER" else 2,
                                                  owner_name=name(2), data={"fingerprint": fp, "as_of": as_of})
            cid = collab_bus.thread_id(bus, f"HYP{hypothesis['id']}")
            hypothesis["correlation_id"] = cid
            collab_bus.post(bus, 2, name(2), "HYPOTHESIS", hypothesis["title"],
                            f"{hypothesis['claim']} — 후보 {json.dumps(hypothesis['candidate'], ensure_ascii=False)}. "
                            f"데이터 기준일 {as_of}, 자동 적용 {'가능(진입 필터 조이기)' if hypothesis['auto_apply'] else '불가(브래킷 규격은 사용자 승인 필요)'}.",
                            to=collab_bus.DESK_VALIDATION, metrics={"axis": hypothesis["axis"]},
                            correlation_id=cid, refs={"hypothesis": hypothesis["id"]}, phase=f["phase"])
            posted.append("HYPOTHESIS")

    validation: Dict[str, Any] = {}
    if hypothesis:
        cid = hypothesis.get("correlation_id") or collab_bus.thread_id(bus, f"HYP{hypothesis['id']}")
        hypothesis["correlation_id"] = cid
        # 6 — 측정 설계
        collab_bus.post(bus, 6, name(6), "VALIDATION_REQUEST", f"{hypothesis['id']} 측정 설계",
                        (f"현행 파라미터를 기준선으로 두고 후보를 같은 데이터({as_of})·같은 70/30 분할로 비교합니다. "
                         f"일봉 경로 근사, 왕복 0.24% 차감, 같은 봉 SL·TP 동시 도달은 손절 우선."),
                        to=[7], correlation_id=cid, metrics={"게이트": buildup.GATE}, phase=f["phase"])
        # 7 — 실제 측정
        ev = buildup.measure(hypothesis, daily)
        measured_now = ev.get("data_fingerprint") == fp
        cand_oos = (ev.get("candidate") or {}).get("oos") or {}
        base_oos = (ev.get("baseline") or {}).get("oos") or {}
        collab_bus.post(bus, 7, name(7), "VALIDATION_RESULT", f"{hypothesis['id']} 실측 결과",
                        (f"후보 OOS {cand_oos.get('n')}건 기대값 {cand_oos.get('ev_pct')}%/건 "
                         f"(기준선 {base_oos.get('ev_pct')}%, Δ{ev.get('delta_oos_ev_pct')}%p) · "
                         f"PF {cand_oos.get('profit_factor')} · MDD {cand_oos.get('mdd_pct')}% "
                         f"(악화 {ev.get('mdd_worsening_pp')}%p) · 전체 표본 "
                         f"{((ev.get('candidate') or {}).get('all') or {}).get('n')}건 · 분할일 {ev.get('cut_date')}"),
                        to=[8, 9], correlation_id=cid,
                        metrics={"ΔEV_OOS": ev.get("delta_oos_ev_pct"), "OOS PF": cand_oos.get("profit_factor"),
                                 "OOS MDD": cand_oos.get("mdd_pct")},
                        refs={"measurement": {k: ev.get(k) for k in ("data_as_of", "cut_date", "delta_oos_ev_pct")}},
                        phase=f["phase"])
        # 8 — 리스크 검토 (게이트 + 하드락)
        gate = hypothesis.get("gate") or {}
        lock = buildup.hard_lock_report()
        approved = bool(gate.get("pass")) and lock["ok"]
        verdict = buildup.apply(doc, hypothesis, by=10) if approved and hypothesis.get("auto_apply") else None
        if approved and hypothesis.get("auto_apply"):
            collab_bus.post(bus, 8, name(8), "APPROVAL", f"{hypothesis['id']} 승인",
                            (f"4개 정량 기준 통과 — {gate.get('reason')}. 하드락 점검 {len(lock['violations'])}건 위반. "
                             f"자동 적용 범위(진입 필터 조이기)에 해당하므로 집행 데스크가 반영합니다."),
                            to=[9, 10], correlation_id=cid,
                            metrics={"게이트": "PASS", "하드락": "OK"}, phase=f["phase"])
        elif approved and not hypothesis.get("auto_apply"):
            buildup.recommend(doc, hypothesis, by=8)
            collab_bus.post(bus, 8, name(8), "APPROVAL", f"{hypothesis['id']} 승인 대기(사용자)",
                            (f"측정은 게이트를 통과했습니다({gate.get('reason')}). 다만 브래킷 규격 변경은 승인 정본"
                             f"(config/swing_approval.json)을 건드리므로 자동 적용하지 않고 승인 대기로 남깁니다."),
                            to=[9], correlation_id=cid, metrics={"게이트": "PASS", "적용": "승인 대기"},
                            phase=f["phase"])
        else:
            hypothesis.setdefault("decisions", []).append(
                {"at_kst": collab_bus._now_kst(), "decision": "HOLD", "by": 8, "reasons": [gate.get("reason")]})
            hypothesis["status"] = "HELD"
            collab_bus.post(bus, 8, name(8), "HOLD", f"{hypothesis['id']} 보류",
                            (f"게이트 미통과 — {gate.get('reason')}. 적용하지 않고 사유를 이력에 남깁니다. "
                             f"다음 데이터 갱신 뒤 같은 후보를 다시 재거나, 다른 후보로 넘어갑니다."),
                            to=[9], correlation_id=cid, metrics={"게이트": "FAIL"}, phase=f["phase"])
        # 10 — 집행·체결 복기
        x = f["execution"]
        exec_detail = (f"모드 {x['mode']} · 보유 {len(x['positions'] or {})}종목 · 대기주문 {len(x['pending'])}건 · "
                       f"당일 실현 ${x['realized_today']} · 예산 ${x['budget']}({x['budget_binding']}) · "
                       f"상태 파일 {x['generated_at']}")
        applied_txt = ""
        if verdict and verdict.get("applied"):
            changes = "; ".join(f"{k} {v.get('from')}→{v.get('to')}" for k, v in (hypothesis.get("changes") or {}).items())
            applied_txt = f" · 적용: {changes}"
        collab_bus.post(bus, 10, name(10), "EXECUTION_REVIEW", f"{hypothesis['id']} 반영·체결 복기",
                        exec_detail + applied_txt,
                        to=[9], correlation_id=cid,
                        metrics={"보유": len(x["positions"] or {}), "예산": x["budget"]}, phase=f["phase"])
        validation = {"hypothesis": hypothesis["id"], "status": hypothesis["status"],
                      "gate": gate.get("pass"), "delta_oos_ev_pct": ev.get("delta_oos_ev_pct"),
                      "applied": bool(verdict and verdict.get("applied"))}
    else:
        # 측정할 새 후보가 없다 — 데이터가 그대로라는 사실을 남기고 끝낸다(가짜 활동 금지).
        subject = "재측정 불필요 — 데이터 동일" if daily else "검증 대기 — 측정할 후보 없음"
        if not _recent_message(bus, subject, hours=6):
            collab_bus.post(bus, 7, name(7), "FEEDBACK", subject,
                            (f"일봉 캐시 기준일 {as_of}(지문 {fp})에서 이번 후보군은 모두 측정 이력이 있습니다. "
                             f"가격 데이터가 갱신되면 같은 후보를 다시 재고, 그 전에는 실전 체결 복기로 빌드업을 이어갑니다."),
                            to=[9, 10], metrics={"데이터 기준일": as_of}, phase=f["phase"])

    # 4) 총괄 요약
    cnt = buildup.counters(doc)
    lock = buildup.hard_lock_report()
    collab_bus.post(bus, 9, name(9), "FEEDBACK", f"[{cycle}사이클] 데스크 종합",
                    (f"가설 누적 {cnt['hypotheses']}건 · 적용 {cnt['applied']} · 승인대기 {cnt['recommended']} "
                     f"· 보류 {cnt['held'] + cnt['rejected']} · 하드락 {'정상' if lock['ok'] else '위반 ' + str(len(lock['violations']))}. "
                     f"태세 {f['ensemble']['stance']} · 국면 {f['macro']['regime']} · 집행 {f['execution']['mode']}."),
                    to=[], metrics={"가설": cnt["hypotheses"], "적용": cnt["applied"]}, phase=f["phase"])

    cloud = f["cloud"] or {}
    if cloud:
        collab_bus.post(bus, 10, name(10), "CLOUD_STATUS", "클라우드 상시 가동 상태",
                        (f"마켓 사이클 {cloud.get('market', {}).get('last_cycle_kst')} "
                         f"({cloud.get('market', {}).get('result')}) · 펄스 {cloud.get('pulse', {}).get('last_cycle_kst')} "
                         f"· 러너 {cloud.get('last_run', {}).get('workflow')}#{cloud.get('last_run', {}).get('run_id')}"),
                        to=[], metrics={"마켓": cloud.get("market", {}).get("result")}, phase=f["phase"])

    cooper = buildup.save(doc)
    bus["duty_every"] = every
    collab_bus.save(bus)

    out = {
        "generated_at_kst": collab_bus._now_kst(),
        "cycle": cycle,
        "phase": f["phase"],
        "session_date": f["session_date"],
        "kst_shift_min": f.get("kst_shift_min"),
        "desks": {
            "research": [agent_profiles.SHORT_NAMES[i] for i in collab_bus.DESK_RESEARCH],
            "validation": [agent_profiles.SHORT_NAMES[i] for i in collab_bus.DESK_VALIDATION],
            "executive": [agent_profiles.SHORT_NAMES[i] for i in collab_bus.DESK_EXECUTIVE],
        },
        "handoffs": handoffs,
        "validation": validation,
        "data": {"as_of": as_of, "fingerprint": fp, "measured_now": measured_now},
        "counters": cooper.get("counters"),
        "hard_lock": {"ok": lock["ok"], "violations": lock["violations"]},
        "cloud": {"market": (f["cloud"] or {}).get("market"), "pulse": (f["cloud"] or {}).get("pulse")},
        "bus": collab_bus.summary(bus),
        "messages_in_bus": len(bus.get("messages") or []),
    }
    with open(CYCLE_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    every = DUTY_EVERY
    if "--every" in args:
        try:
            every = int(args[args.index("--every") + 1])
        except Exception:
            every = DUTY_EVERY
    out = run_cycle(every=every)
    v = out.get("validation") or {}
    print(f"[collab] cycle {out['cycle']} {out['generated_at_kst']} phase={out['phase']} · "
          f"데이터 {out['data']['as_of']}({out['data']['fingerprint']})")
    print(f"  핸드오프 {len(out['handoffs'])}건 · 가설 {v.get('hypothesis', '—')} "
          f"{v.get('status', '')} · 게이트 {'PASS' if v.get('gate') else ('FAIL' if v else '—')} "
          f"· 적용 {v.get('applied')}")
    print(f"  버스 {out['bus']['total']}건 (스레드 {out['bus']['threads']}, 인계 {out['bus']['handoffs']}) · "
          f"하드락 {'OK' if out['hard_lock']['ok'] else out['hard_lock']['violations']}")
    if "--json" in args:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
