#!/usr/bin/env python3
"""
CUBE Work Feed Generator — dynamic, 24/7

Before: a 900-line literal list of posts. Ten agents "reported" the same 11-11
numbers every hour, with timestamps like "22:38" stamped onto whatever day it
was, and directives mixed into fresh updates.

Now: the orchestrator runs all ten agents against live data, and this script
turns each agent's real report into one feed bubble. Directives are kept
separately, pinned, with the date they were actually given.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from agents.cube_orchestrator import CubeOrchestrator
from core import market_clock, market_snapshot, ledger as ledger_mod

DATA = os.path.join(BASE_DIR, "data")
DOCS = os.path.join(BASE_DIR, "docs")
FEED_PATH = os.path.join(DATA, "agent_feed.json")
DOCS_FEED_PATH = os.path.join(DOCS, "agent_feed.json")
DIRECTIVES = os.path.join(DATA, "directives_archive.json")

AGENT_STYLE = {
    1: {"icon": "📰", "avatar_bg": "from-cyan-500 to-blue-600", "badge_color": "bg-cyan-500/10 text-cyan-400 border-cyan-500/20", "category": "SENTIMENT"},
    2: {"icon": "📈", "avatar_bg": "from-blue-600 to-indigo-600", "badge_color": "bg-blue-500/10 text-blue-400 border-blue-500/20", "category": "TECHNICAL"},
    3: {"icon": "🏢", "avatar_bg": "from-emerald-500 to-teal-600", "badge_color": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", "category": "CATALYST"},
    4: {"icon": "🌐", "avatar_bg": "from-purple-600 to-violet-700", "badge_color": "bg-purple-500/10 text-purple-400 border-purple-500/20", "category": "MACRO"},
    5: {"icon": "🐋", "avatar_bg": "from-amber-500 to-orange-600", "badge_color": "bg-amber-500/10 text-amber-400 border-amber-500/20", "category": "GAMMA"},
    6: {"icon": "📐", "avatar_bg": "from-indigo-500 to-fuchsia-600", "badge_color": "bg-indigo-500/10 text-indigo-400 border-indigo-500/20", "category": "QUANT"},
    7: {"icon": "🧪", "avatar_bg": "from-rose-500 to-red-600", "badge_color": "bg-rose-500/10 text-rose-400 border-rose-500/20", "category": "BACKTEST"},
    8: {"icon": "🎯", "avatar_bg": "from-teal-500 to-emerald-600", "badge_color": "bg-teal-500/10 text-teal-400 border-teal-500/20", "category": "ALPHA"},
    9: {"icon": "🧬", "avatar_bg": "from-violet-500 to-purple-600", "badge_color": "bg-violet-500/10 text-violet-400 border-violet-500/20", "category": "ENSEMBLE"},
    10: {"icon": "⚡", "avatar_bg": "from-yellow-400 to-amber-600", "badge_color": "bg-amber-500/10 text-amber-400 border-amber-500/20", "category": "EXECUTION"},
}
NAMES = {
    1: "Agent 1: 뉴스 & 증권사 인사이트 센티먼트 분석가",
    2: "Agent 2: 기술적 일중 변동성 & 모멘텀 분석가",
    3: "Agent 3: 단기 실적 촉매(Catalyst) & 어닝 서프라이즈 분석가",
    4: "Agent 4: 글로벌 매크로 & 외환(FX)·VIX 레짐 분석가",
    5: "Agent 5: 스마트머니 & 기관 옵션 감마 분석가",
    6: "Agent 6: 단기 퀀트 시나리오 모델링 연구원",
    7: "Agent 7: 단기 시나리오 백테스트 검증 엔지니어",
    8: "Agent 8: 단기 수익 시나리오 알파 분석가",
    9: "Agent 9: 단기 최적 시나리오 융합가",
    10: "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
}
ROLES = {1: "NEWS_SENTIMENT_SPECIALIST", 2: "TECH_MOMENTUM_SPECIALIST", 3: "FUNDAMENTALS_CATALYST_SPECIALIST",
         4: "GLOBAL_MACRO_FX_SPECIALIST", 5: "SMART_MONEY_GAMMA_SPECIALIST", 6: "QUANT_SCENARIO_MODELER",
         7: "SHORT_TERM_BACKTESTER_ENGINEER", 8: "SHORT_TERM_ALPHA_EVALUATOR", 9: "SHORT_TERM_ENSEMBLE_SYNTHESIZER",
         10: "SHORT_TERM_EXECUTION_TRADER"}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def _fmt(x, digits=2, suffix=""):
    try:
        return f"{float(x):,.{digits}f}{suffix}"
    except Exception:
        return "—"


def _post(agent_id: int, clock: Dict[str, Any], badge: str, title: str, content: str,
          metrics: List[Dict[str, Any]], action: str, pinned: bool = False, ts: datetime = None) -> Dict[str, Any]:
    st = AGENT_STYLE[agent_id]
    ts = ts or _now_kst()
    return {
        "id": f"post-{agent_id:02d}-{ts.strftime('%Y%m%d%H')}",
        "agent_id": agent_id,
        "agent_name": NAMES[agent_id],
        "agent_role": ROLES[agent_id],
        "icon": st["icon"], "avatar_bg": st["avatar_bg"], "badge_color": st["badge_color"],
        "timestamp": ts.strftime("%Y-%m-%d %H:%M"),
        "time_ago": "방금 전",
        "category": st["category"],
        "badge": badge,
        "title": title,
        "content": content,
        "metrics": metrics,
        "action_required": action,
        "reactions": {},
        "pinned": pinned,
        "phase": clock.get("phase"),
    }


# ------------------------------------------------------------------ per-agent posts
def post_agent1(r: Dict[str, Any], clock) -> Dict[str, Any]:
    tr = r.get("ticker_reports", {})
    lines = []
    for tk in ["SOXL", "TQQQ", "NVDA", "TSLA", "SGOV"]:
        t = tr.get(tk, {})
        br = t.get("dynamic_exit_adjustment", {})
        head = (t.get("breaking_news") or ["—"])[0]
        lines.append(f"• {tk} {t.get('sentiment_score')}점 ({t.get('sentiment_grade','').split(' (')[0]}) — {head[:95]}")
        lines.append(f"    → {br.get('action','').split(' (')[0]} · TP1 {br.get('tp1')} / SL {br.get('sl')}")
    macro = r.get("macro_news_flow", {})
    macro_lines = []
    for topic, label in (("FED_RATES", "금리"), ("TARIFF_TRADE", "관세·수출통제"), ("CRYPTO", "크립토"), ("AI_CAPEX", "AI CAPEX")):
        rows = macro.get(topic) or []
        if rows:
            macro_lines.append(f"• [{label}] {rows[0]['title'][:90]} ({rows[0].get('source')})")
    wk = r.get("weekend_block", {})
    content = (
        f"실시간 뉴스 {r.get('data_sources', {}).get('news_items')}건(24시간 내 {r.get('data_sources', {}).get('items_last_24h')}건)을 "
        f"수집해 종목별로 점수화했습니다. 시장 내재 리스크 점수 {r.get('market_implied_anchor', {}).get('score')}/100 "
        f"({r.get('market_implied_anchor', {}).get('label')})와 블렌딩한 종합 센티먼트는 {r.get('average_sentiment_score')}점, "
        f"판정 {r.get('overall_market_mood')}.\n\n" + "\n".join(lines) + "\n\n"
        "[매크로 헤드라인]\n" + ("\n".join(macro_lines) if macro_lines else "• 특이 매크로 헤드라인 없음") + "\n\n"
        f"[주말 데이터] BTC {_fmt(wk.get('crypto_24_7', {}).get('btc'))} ({_fmt(wk.get('crypto_24_7', {}).get('btc_change_pct'))}%), "
        f"ETH {_fmt(wk.get('crypto_24_7', {}).get('eth'))} ({_fmt(wk.get('crypto_24_7', {}).get('eth_change_pct'))}%), "
        f"원/달러 {_fmt(wk.get('fx_open', {}).get('usd_krw'))}원. 주식·선물은 휴장이라 마지막 종가 기준입니다."
    )
    return _post(1, clock, "📰 실시간 뉴스·센티먼트 (하드코딩 제거)", 
                 f"[{clock['now_kst'][11:16]} 뉴스 스캔] 종목 평균 {r.get('average_sentiment_score')}점 · 시장 내재 {r.get('market_implied_anchor', {}).get('score')}점 → {r.get('overall_market_mood')}",
                 content,
                 [{"label": "뉴스 수집", "value": f"{r.get('data_sources', {}).get('news_items')}건", "highlight": True},
                  {"label": "피드 정상", "value": r.get("data_sources", {}).get("news_feeds_ok")},
                  {"label": "SOXL", "value": f"{tr.get('SOXL', {}).get('sentiment_score')}점"},
                  {"label": "NVDA", "value": f"{tr.get('NVDA', {}).get('sentiment_score')}점"}],
                 "브래킷 값은 core/exit_planner가 실측 ATR·센티먼트로 재계산합니다.")


def post_agent2(r: Dict[str, Any], clock) -> Dict[str, Any]:
    lv = r.get("levels", {})
    rows = []
    for sym in ["SOXL", "TQQQ", "NVDA", "SOXS", "SQQQ"]:
        v = lv.get(sym)
        if not v:
            continue
        rows.append(f"• {sym} ${_fmt(v.get('close'))} | ATR {v.get('atr_pct')}% | RSI2 {v.get('rsi2')} | %B {v.get('pct_b')} | "
                    f"RVOL {v.get('rvol')} | 진입점수 {v.get('entry_score')} ({v.get('entry_signal')})")
        rows.append(f"    돌파 ${v.get('breakout_trigger')} (+{v.get('breakout_pct')}%) / 이탈 ${v.get('breakdown_trigger')}")
    gap = (lv.get("SOXL") or {}).get("gap_plan") or {}
    dip = r.get("dip_swing_watchlist") or {}
    oa = dip.get("overnight_approval") or {}
    head = (f"\n\n[DIP_SWING 오버나이트 {oa.get('status')}] {oa.get('entry_rule')} · 최대 {oa.get('max_hold_sessions')}세션 · "
            f"1회 리스크 {oa.get('risk_per_trade_pct')}% · 최대 {oa.get('max_positions')}종목" if oa else "")
    dip_txt = head
    if dip.get("signals"):
        dip_txt += "\n\n[DIP_SWING 신호]\n" + "\n".join(
            f"• {s['symbol']} ${s['close']} — RSI2 {s['rsi2']}, %B {s['pct_b']}, 검증 TP ${s['validated_tp']} / SL ${s['validated_sl']}"
            for s in dip["signals"])
    elif dip.get("watch"):
        dip_txt += "\n\n[DIP_SWING 관찰]\n" + "\n".join(
            f"• {w['symbol']} RSI2 {w.get('rsi2')} · %B {w.get('pct_b')} · RVOL {w.get('rvol')} → 미충족"
            for w in dip["watch"][:2])
    content = ("실측 일봉으로 계산한 종목별 레벨입니다.\n\n" + "\n".join(rows) +
               (f"\n\n[월요일 개장 갭 시나리오] 금요일 종가 ${_fmt(gap.get('friday_close'))}, "
                f"선물 내재 갭 {_fmt(gap.get('futures_implied_gap_pct'))}% → 주경로 ${_fmt(gap.get('primary_open'))}, "
                f"대안 ${_fmt(gap.get('alternate_open'))}. ATR 밴드 ${_fmt((gap.get('atr_band') or [None])[0])}~${_fmt((gap.get('atr_band') or [None, None])[1])}. "
                f"{gap.get('plan','')}" if gap else "") + dip_txt +
               f"\n\n스몰캡 급등주 모델은 11-13 지시로 실전 배제 상태입니다.")
    return _post(2, clock, "📈 레벨·진입신호 (실측 OHLCV)",
                 f"[{clock['now_kst'][11:16]} 기술적 스캔] {', '.join(r.get('actionable_symbols') or ['진입 신호 없음'])} · 최고 {r.get('best_setup', {}).get('symbol')}({r.get('best_setup', {}).get('score')}점)",
                 content,
                 [{"label": "SOXL ATR", "value": f"{lv.get('SOXL', {}).get('atr_pct')}%", "highlight": True},
                  {"label": "SOXL 돌파", "value": f"${lv.get('SOXL', {}).get('breakout_trigger')}", "highlight": True},
                  {"label": "진입 신호", "value": ", ".join(r.get("actionable_symbols") or ["없음"])},
                  {"label": "DIP 신호", "value": f"{len(dip.get('signals') or [])}건"}],
                 "개장 후 돌파 트리거 확인 시에만 진입합니다.")


def post_agent3(r: Dict[str, Any], clock) -> Dict[str, Any]:
    er = r.get("earnings_calendar", [])[:4]
    ev = r.get("macro_calendar", [])[:3]
    rows = [f"• {e['ticker']} D-{e['d_day']} · {e['report_kst']} · EPS 컨센 {_fmt(e.get('eps_estimate'), 2)}" for e in er]
    evs = [f"• {e['event']} D-{e['d_day']} ({e['date_kst']}) — {e.get('note','')}" for e in ev]
    content = ("Yahoo Finance 실적 캘린더를 KST로 변환한 D-day입니다. 미국 장 마감 후 발표는 다음 날 아침 KST로 표기합니다.\n\n"
               + "\n".join(rows) + "\n\n[공식 매크로 일정]\n" + "\n".join(evs))
    nvda = next((e for e in er if e["ticker"] == "NVDA"), None)
    return _post(3, clock, "🏢 실적·매크로 캘린더 (KST 변환)",
                 f"[{clock['now_kst'][11:16]} 촉매 점검] " + (f"NVDA 실적 D-{nvda['d_day']} ({nvda['report_kst']})" if nvda else "주요 실적 일정"),
                 content,
                 [{"label": "NVDA 실적", "value": f"D-{nvda['d_day']}" if nvda else "—", "highlight": True},
                  {"label": "EPS 컨센", "value": _fmt(nvda.get("eps_estimate"), 2) if nvda else "—"},
                  {"label": "매크로 일정", "value": f"{len(r.get('macro_calendar', []))}건"},
                  {"label": "촉매 헤드라인", "value": f"{len(r.get('headline_catalysts', []))}건"}],
                 (nvda["rule"] if nvda else "일정 기반 포지션 규칙 유지"))


def post_agent4(r: Dict[str, Any], clock) -> Dict[str, Any]:
    gi = r.get("global_indices", {})
    cr = r.get("crypto", {})
    content = (
        f"VIX {_fmt(r.get('vix_level'))} ({_fmt(r.get('vix_change_pct'))}%), DXY {_fmt(r.get('dxy'))} ({_fmt(r.get('dxy_change_pct'))}%), "
        f"미 10년 {_fmt(r.get('us_10y_yield'), 3)}% / 3개월 {_fmt(r.get('us_3m_yield'), 3)}% (기울기 {r.get('curve_slope_bp')}bp).\n\n"
        f"• 항셍 {_fmt(gi.get('hang_seng', {}).get('level'))} ({_fmt(gi.get('hang_seng', {}).get('change_pct'))}%)\n"
        f"• 닛케이 {_fmt(gi.get('nikkei_225', {}).get('level'))} ({_fmt(gi.get('nikkei_225', {}).get('change_pct'))}%)\n"
        f"• DAX {_fmt(gi.get('dax_40', {}).get('level'))} ({_fmt(gi.get('dax_40', {}).get('change_pct'))}%)\n"
        f"• 나스닥 선물 {_fmt(gi.get('nasdaq_fut', {}).get('level'))} ({_fmt(gi.get('nasdaq_fut', {}).get('change_pct'))}%)\n"
        f"• BTC {_fmt(cr.get('btc'))} ({_fmt(cr.get('btc_change_pct'))}%) · ETH {_fmt(cr.get('eth'))} ({_fmt(cr.get('eth_change_pct'))}%)\n\n"
        f"레짐 판정 {r.get('macro_regime')}. 레버리지 매매 {'가능' if r.get('safe_for_leverage') else '제한'}. "
        f"리스크오프 트리거 {len(r.get('risk_off_triggers', []))}개 발화"
        + (": " + "; ".join(r.get("risk_off_triggers", [])) if r.get("risk_off_triggers") else "")
    )
    return _post(4, clock, "🌐 글로벌 매크로·FX·VIX (실시간 시세)",
                 f"[{clock['now_kst'][11:16]} 매크로] VIX {_fmt(r.get('vix_level'))} · DXY {_fmt(r.get('dxy'))} · 레짐 {r.get('macro_regime')}",
                 content,
                 [{"label": "VIX", "value": _fmt(r.get("vix_level")), "highlight": True},
                  {"label": "DXY", "value": _fmt(r.get("dxy")), "highlight": True},
                  {"label": "10Y", "value": f"{_fmt(r.get('us_10y_yield'), 3)}%"},
                  {"label": "레짐", "value": r.get("macro_regime")}],
                 "VIX 22 이상 또는 DXY 104.5 이상이면 레버리지 진입을 자동 제한합니다.")


def post_agent5(r: Dict[str, Any], clock) -> Dict[str, Any]:
    chains = r.get("chains", {})
    rows = []
    for sym, c in chains.items():
        rows.append(f"• {sym} ({c.get('expiry')}): Put/Call OI {c.get('put_call_oi')} · 거래량비 {c.get('put_call_volume')} · "
                    f"감마월 ${_fmt(c.get('gamma_wall_strike'))} · 맥스페인 ${_fmt(c.get('max_pain_strike'))}")
    content = ("실제 옵션 체인 미결제약정 기준입니다. 소셜(레딧·X) 데이터는 연동되어 있지 않아 수치를 만들지 않습니다.\n\n"
               + ("\n".join(rows) if rows else "체인 조회 실패 — 다음 주기 재시도") +
               f"\n\n데이터 시점: {r.get('data_as_of')} ({r.get('stale_note')})")
    return _post(5, clock, "🐋 기관 옵션 포지셔닝",
                 f"[{clock['now_kst'][11:16]} 옵션] QQQ Put/Call {r.get('qqq_put_call_oi')} → {r.get('institutional_bias')}",
                 content,
                 [{"label": "QQQ P/C OI", "value": r.get("qqq_put_call_oi"), "highlight": True},
                  {"label": "NVDA 감마월", "value": f"${_fmt(r.get('nvda_gamma_wall'))}"},
                  {"label": "NVDA 맥스페인", "value": f"${_fmt(r.get('nvda_max_pain'))}"},
                  {"label": "판정", "value": (r.get("institutional_bias") or "—").split(" (")[0]}],
                 "감마월 하회 시 변동성 확대 구간이므로 진입을 분할합니다.")


def post_agent6(r: Dict[str, Any], clock) -> Dict[str, Any]:
    wb = r.get("weekend_playbook")
    rows = [f"• {s['id']} {s['name']}: {s['validated']['verdict']} (PF {s['validated']['PF']})" for s in r.get("scenarios", [])]
    wtxt = ""
    if wb:
        wtxt = ("\n\n[월요일 개장 플레이북] 금요일 종가 " + _fmt(wb.get("friday_close")) +
                f", 선물 내재 갭 {_fmt(wb.get('futures_implied_gap_pct'))}%\n" +
                "\n".join(f"• {c['case']}: 기준가 ${_fmt(c['open_ref'])} — {c['play']} [{c['bracket']}]" for c in wb["scenarios"]))
    content = (f"10개 시나리오를 실측 ATR(SOXL {r.get('atr_basis', {}).get('SOXL_atr_pct')}%, "
               f"TQQQ {r.get('atr_basis', {}).get('TQQQ_atr_pct')}%) 기준으로 재정규화했습니다.\n\n" + "\n".join(rows) + wtxt)
    return _post(6, clock, "📐 ATR 재정규화 시나리오" + (" + 주말 플레이북" if wb else ""),
                 f"[{clock['now_kst'][11:16]} 시나리오] 유효 전략 {', '.join(r.get('approved_now', [])) or '없음'}" + (" · 월요일 3개 갭 시나리오" if wb else ""),
                 content,
                 [{"label": "SOXL ATR", "value": f"{r.get('atr_basis', {}).get('SOXL_atr_pct')}%", "highlight": True},
                  {"label": "유효 시나리오", "value": f"{len(r.get('approved_now', []))}개"},
                  {"label": "개장 플레이북", "value": "준비" if wb else "—"}],
                 "시나리오는 방향 판단이 아니라 브래킷과 트리거로만 사용합니다.")


def post_agent7(r: Dict[str, Any], clock) -> Dict[str, Any]:
    rows = []
    for s in r.get("backtest_results", []):
        m = s.get("metrics") or {}
        rows.append(f"• {s['name']}: {m.get('total_return_pct')}% · PF {m.get('profit_factor')} · 승률 {m.get('win_rate_pct')}% "
                    f"· Sharpe {m.get('sharpe_ratio')} · MDD {m.get('mdd_pct')}% · {m.get('total_trades')}회 → {s['status']}")
    ch = r.get("bear_engine_choice") or {}
    content = ("60분봉 경로 기준 시뮬레이션(왕복 0.24% 마찰, 손절 우선 체결 가정)입니다. 실전 원장과 분리된 수치입니다.\n\n"
               + "\n".join(rows)
               + (f"\n\n[숏 엔진] VIX ≥ {ch.get('vix_trigger')} + 확인 {ch.get('confirmations_required')}개 조건이 "
                  f"Calmar 기준 최적으로 나타났습니다." if ch else "")
               + f"\n\n보고서 생성: {r.get('report_generated_at_kst')} ({r.get('report_age_hours')}시간 전)")
    return _post(7, clock, "🧪 시간봉 경로 백테스트 (시뮬레이션)",
                 f"[{clock['now_kst'][11:16]} 검증] {r.get('key_takeaway','')[:110]}",
                 content,
                 [{"label": "현행 장중", "value": f"{(r.get('backtest_results') or [{}])[0].get('metrics', {}).get('total_return_pct')}%"},
                  {"label": "개선 장중", "value": f"{(r.get('backtest_results') or [{}, {}])[1].get('metrics', {}).get('total_return_pct')}%"},
                  {"label": "DIP_SWING", "value": f"{(r.get('backtest_results') or [{}, {}, {}])[2].get('metrics', {}).get('total_return_pct')}%", "highlight": True},
                  {"label": "숏 트리거", "value": f"VIX {ch.get('vix_trigger')}" if ch else "—"}],
                 "백테스트 수치는 원장에 반영하지 않습니다.")


def post_agent8(r: Dict[str, Any], clock) -> Dict[str, Any]:
    ap = r.get("profitable_scenarios", [])
    rj = r.get("rejected_scenarios", [])
    rows = [f"• 승인 {a['id']} (PF {a['pf']}, Sharpe {a['sharpe']}, MDD {a['mdd_pct']}%) — {a.get('alpha_source','')}" for a in ap]
    rej = [f"• 폐기 {x['id']}: {x.get('reject_reason')}" for x in rj]
    content = (f"승인 기준: PF ≥ {r['criteria']['profit_factor_min']}, Sharpe ≥ {r['criteria']['sharpe_min']}, "
               f"MDD ≤ {r['criteria']['max_dd_pct']}%, 거래 ≥ {r['criteria']['min_trades']}회.\n\n"
               + ("\n".join(rows) if rows else "• 승인 통과 전략 없음") + "\n\n[폐기]\n" + "\n".join(rej if rej else ["• 없음"]))
    return _post(8, clock, "🎯 알파·리스크 판정",
                 f"[{clock['now_kst'][11:16]} 알파] 승인 {len(ap)}개 / 폐기 {len(rj)}개 · 실행 모드 {r.get('recommended_execution_mode')}",
                 content,
                 [{"label": "승인", "value": f"{len(ap)}개", "highlight": True},
                  {"label": "폐기", "value": f"{len(rj)}개"},
                  {"label": "실행 모드", "value": r.get("recommended_execution_mode")}],
                 r.get("approval_note", ""))


def post_agent9(r: Dict[str, Any], clock) -> Dict[str, Any]:
    w = r.get("target_portfolio_weights", {})
    alloc = r.get("target_alloc_usd", {})
    content = (f"레짐 {r.get('regime')} / VIX {r.get('vix')}. {r.get('regime_note')}\n\n" +
               "\n".join(f"• {k}: {v*100:.0f}% (${_fmt(alloc.get(k))} / {_fmt(r.get('target_alloc_krw', {}).get(k), 0)}원)"
                         for k, v in w.items() if v) +
               f"\n\n승인 전략: {', '.join(r.get('approved_strategies', [])) or '없음'}")
    return _post(9, clock, "🧬 앙상블 비중",
                 f"[{clock['now_kst'][11:16]} 비중] " + " / ".join(f"{k} {v*100:.0f}%" for k, v in w.items() if v),
                 content,
                 [{"label": k, "value": f"{v*100:.0f}%", "highlight": k == "SOXL"} for k, v in w.items() if v][:4],
                 "목표 비중은 상한이며 실제 주문은 개장 후 신호로 확정합니다.")


def post_agent10(r: Dict[str, Any], clock) -> Dict[str, Any]:
    led = r.get("ledger", {})
    br = r.get("bracket_review", [])
    plan = r.get("planned_actions", [])
    rows = []
    for b in br:
        rc = b.get("recomputed") or {}
        cur = b.get("current_bracket") or {}
        if not rc:
            rows.append(f"• {b['ticker']}: {b['status']} · 체결가 ${_fmt(b.get('entry_fill'))}")
            continue
        rows.append(f"• {b['ticker']}: {b['status']}{' (사용자 지정 레벨)' if b.get('levels_locked') else ''} · "
                    f"체결가 ${_fmt(b.get('entry_fill'))} → 현재 TP1 ${_fmt(cur.get('tp1'))} / SL ${_fmt(cur.get('sl'))} "
                    f"| 재산정 TP1 ${_fmt(rc.get('tp1'))} / TP2 ${_fmt(rc.get('tp2'))} / SL ${_fmt(rc.get('sl'))} (ATR {rc.get('atr_pct')}%)")
    ptxt = [f"• {p['ticker']} {p['action']} — {p.get('status')}" for p in plan] or ["• 대기 주문 없음"]
    sw = r.get("swing_mode") or {}
    oa = sw.get("overnight_approval") or {}
    cap = r.get("capital_plan") or {}
    swing_txt = ""
    if oa:
        swing_txt = (f"\n\n[DIP_SWING 오버나이트 {oa.get('status')}] {oa.get('entry_rule')} · 최대 {oa.get('max_hold_sessions')}세션 · "
                     f"1회 리스크 {oa.get('risk_per_trade_pct')}% · 최대 {oa.get('max_positions')}종목 · 상한 ${_fmt(cap.get('dip_swing_cap_usd'))}" +
                     (f"\n승인 {oa.get('approved_at_kst')} ({oa.get('approved_by')}) · {oa.get('execution_mode')}" if oa.get("approved") else ""))
    content = (
        f"원장 정합성 {r.get('consistency_check', {}).get('status')} "
        f"(이슈 {len(r.get('consistency_check', {}).get('issues', []))}건, 거래일 플래그 "
        f"{len(r.get('consistency_check', {}).get('fixed_trading_day_flags', []))}건 수정).\n"
        f"실현 +${_fmt(led.get('realized_pnl_usd'))} / 미실현 ${_fmt(led.get('unrealized_pnl_usd'))} / "
        f"평가 ${_fmt(led.get('equity_usd'))} ({_fmt(led.get('equity_krw'), 0)}원)\n"
        f"현금 ${_fmt(led.get('cash_usd'))} / {_fmt(led.get('cash_krw'), 0)}원 ({led.get('cash_pct')}%) · 환율 {led.get('fx_rate')}원 "
        f"({led.get('fx_source')})\n\n[브래킷 재산정]\n" + ("\n".join(rows) if rows else "• 활성 브래킷 없음") +
        "\n\n[개장 대기 계획]\n" + "\n".join(ptxt) + swing_txt +
        f"\n\n다음 개장 {r.get('next_open_kst')} · 상태 {r.get('execution_status')}"
    )
    return _post(10, clock, "⚡ 원장·브래킷·개장 계획",
                 f"[{clock['now_kst'][11:16]} 집행] 현금 {_fmt(led.get('cash_pct'))}% (${_fmt(led.get('cash_usd'))}) · 다음 개장 {r.get('next_open_kst','')} · 계획 {len(plan)}건",
                 content,
                 [{"label": "평가자산", "value": f"${_fmt(led.get('equity_usd'))}", "highlight": True},
                  {"label": "현금", "value": f"{_fmt(led.get('cash_usd'))}"},
                  {"label": "누적 실현", "value": f"+${_fmt(led.get('realized_pnl_usd'))}"},
                  {"label": "승률", "value": f"{led.get('win_rate_pct')}%"}],
                 "주문은 개장 세션에서 신호 확인 후 집행합니다. DIP_SWING 오버나이트는 승인된 규칙(진입 조건·최대 3세션·리스크 2%)으로 집행합니다.")


def directive_posts(clock) -> List[Dict[str, Any]]:
    try:
        with open(DIRECTIVES, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        items = []
    posts = []
    for i, d in enumerate(items):
        posts.append({
            "id": f"post-directive-{i:03d}",
            "agent_id": 10,
            "agent_name": "박동훈 TL 지시 기록",
            "agent_role": "SYSTEM_DIRECTIVE",
            "icon": "📌",
            "avatar_bg": "from-red-600 to-amber-600",
            "badge_color": "bg-red-500/20 text-red-400 border-red-500/40",
            "timestamp": d.get("timestamp"),
            "time_ago": "지시 기록",
            "category": "DIRECTIVE",
            "badge": d.get("badge", "TL 지시"),
            "title": d.get("title", ""),
            "content": d.get("content", ""),
            "metrics": d.get("metrics", []),
            "action_required": d.get("action", "이행 상태는 해당 에이전트 보고에 반영"),
            "reactions": {},
            "pinned": True,
            "phase": "ARCHIVE",
        })
    return posts


def main():
    clock = market_clock.resolve()
    orch = CubeOrchestrator()
    report = orch.run_daily_pipeline(quiet=True)
    agents = {a["agent_id"]: a.get("report", {}) for a in report.get("agents", [])}

    posts = []
    for aid, fn in ((1, post_agent1), (2, post_agent2), (3, post_agent3), (4, post_agent4), (5, post_agent5),
                    (6, post_agent6), (7, post_agent7), (8, post_agent8), (9, post_agent9), (10, post_agent10)):
        try:
            posts.append(fn(agents.get(aid, {}), clock))
        except Exception as e:
            posts.append(_post(aid, clock, "⚠️ 보고 생성 실패", f"Agent {aid} 보고 실패: {e}", str(e), [], "로그 확인"))

    posts.extend(directive_posts(clock))

    # preserve manually recorded live-execution posts
    if os.path.exists(FEED_PATH):
        try:
            with open(FEED_PATH, "r", encoding="utf-8") as f:
                prev = json.load(f).get("posts", [])
            live = [p for p in prev if str(p.get("id", "")).startswith("post-live-")]
            posts = live + posts
        except Exception:
            pass

    snap = report.get("agents") and market_snapshot.load_snapshot()
    q = snap.get("quotes", {})

    def gm(sym):
        d = q.get(sym) or {}
        return {"level": d.get("last") or 0.0, "change": f"{d.get('change_pct') or 0:+.2f}%"}

    feed = {
        "title": "CUBE 10-Agent Work Feed & Global Macro Room",
        "last_updated": clock["now_kst"] + " KST",
        "phase": clock["phase"],
        "phase_label": clock["label_ko"],
        "session_mode": "24/7 — 모든 세션에서 10인 전원 가동",
        "next_open_kst": clock["next_open_kst"],
        "hours_to_next_open": clock["hours_to_next_open"],
        "premarket_status": ("정규장 운영" if clock["phase"] == "REGULAR" else
                             "프리마켓" if clock["phase"] == "PRE_MARKET" else
                             "주말 준비 모드" if clock["phase"] == "WEEKEND_PREP" else clock["label_ko"]),
        "active_monitoring_agents": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "agents_reporting": report.get("agents_ok"),
        "agents_total": report.get("total_agents"),
        "data_provenance": report.get("data_provenance"),
        "pipeline_seconds": report.get("pipeline_seconds"),
        "global_macro": {
            "hang_seng": gm("^HSI"), "nikkei_225": gm("^N225"), "dax_40": gm("^GDAXI"),
            "dxy_dollar_index": gm("DX-Y.NYB"), "usd_krw": gm("KRW=X"), "usd_jpy": gm("JPY=X"),
            "nasdaq_fut": gm("NQ=F"), "vix": gm("^VIX"),
        },
        "total_posts": len(posts),
        "posts": posts,
    }

    for p in (FEED_PATH, DOCS_FEED_PATH):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(feed, f, ensure_ascii=False, indent=2)

    print(f"✅ feed updated {feed['last_updated']} [{clock['phase']}] posts {len(posts)} "
          f"(agents {report.get('agents_ok')}/{report.get('total_agents')})")


if __name__ == "__main__":
    main()
