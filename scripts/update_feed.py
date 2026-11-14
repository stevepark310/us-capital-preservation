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
    1: "Agent 1: 뉴스 & 센티먼트 분석가 (주식·채권·외환·크립토 분리)",
    2: "Agent 2: 기술적 변동성 & 모멘텀 분석가 (주식·채권·환율·코인)",
    3: "Agent 3: 단기 실적 촉매(Catalyst) & 어닝 서프라이즈 분석가",
    4: "Agent 4: 글로벌 매크로 · 채권 · 외환 · 선행지표 총괄",
    5: "Agent 5: 스마트머니 · 크레딧 · 기관 포지셔닝 분석가",
    6: "Agent 6: 단기 퀀트 시나리오 모델링 연구원",
    7: "Agent 7: 단기 시나리오 백테스트 검증 엔지니어",
    8: "Agent 8: 단기 수익 시나리오 알파 분석가",
    9: "Agent 9: 단기 최적 시나리오 융합가",
    10: "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
}
ROLES = {1: "NEWS_SENTIMENT_SPECIALIST", 2: "TECH_MOMENTUM_MULTIASSET", 3: "FUNDAMENTALS_CATALYST_SPECIALIST",
         4: "GLOBAL_MACRO_RATES_FX_CRYPTO_LEI", 5: "SMART_MONEY_GAMMA_SPECIALIST", 6: "QUANT_SCENARIO_MODELER",
         7: "SHORT_TERM_BACKTESTER_ENGINEER", 8: "CROSS_MARKET_ALPHA_EVALUATOR", 9: "CROSS_MARKET_ENSEMBLE",
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
    sec = r.get("sector_sentiment", {})
    sec_lines = []
    for key in ("BONDS_FED", "FX", "CRYPTO"):
        s = sec.get(key) or {}
        if not s:
            continue
        top = (s.get("top_headlines") or [{}])[0].get("title", "")
        sec_lines.append(f"• {s.get('label')}: {s.get('sentiment_score')}점 ({s.get('grade','').split(' (')[0]}) · "
                         f"헤드라인 {s.get('headline_count')}건 (+{s.get('positive')}/-{s.get('negative')}) — {top[:70]}")
    wk = r.get("weekend_block", {})
    content = (
        f"실시간 뉴스 {r.get('data_sources', {}).get('news_items')}건(24시간 내 {r.get('data_sources', {}).get('items_last_24h')}건)을 "
        f"수집해 종목별로 점수화했습니다. 시장 내재 리스크 점수 {r.get('market_implied_anchor', {}).get('score')}/100 "
        f"({r.get('market_implied_anchor', {}).get('label')})와 블렌딩한 종합 센티먼트는 {r.get('average_sentiment_score')}점, "
        f"판정 {r.get('overall_market_mood')}.\n\n" + "\n".join(lines) + "\n\n"
        "[섹터별 분리 센티먼트 — 채권/연준 · 외환 · 크립토]\n" + ("\n".join(sec_lines) or "• 해당 섹터 헤드라인 없음") + "\n\n"
        "[매크로 헤드라인]\n" + ("\n".join(macro_lines) if macro_lines else "• 특이 매크로 헤드라인 없음") + "\n\n"
        f"[주말 데이터] BTC {_fmt(wk.get('crypto_24_7', {}).get('btc'))} ({_fmt(wk.get('crypto_24_7', {}).get('btc_change_pct'))}%), "
        f"ETH {_fmt(wk.get('crypto_24_7', {}).get('eth'))} ({_fmt(wk.get('crypto_24_7', {}).get('eth_change_pct'))}%), "
        f"원/달러 {_fmt(wk.get('fx_open', {}).get('usd_krw'))}원. 주식·선물은 휴장이라 마지막 종가 기준입니다."
    )
    bond_s = (sec.get("BONDS_FED") or {}).get("sentiment_score")
    fx_s = (sec.get("FX") or {}).get("sentiment_score")
    crypto_s = (sec.get("CRYPTO") or {}).get("sentiment_score")
    return _post(1, clock, "📰 뉴스·센티먼트 (주식+채권+외환+크립토 분리)",
                 f"[{clock['now_kst'][11:16]} 뉴스 스캔] 종목 평균 {r.get('average_sentiment_score')}점 · 채권 {bond_s} / 외환 {fx_s} / 코인 {crypto_s} · 시장 내재 {r.get('market_implied_anchor', {}).get('score')}점",
                 content,
                 [{"label": "뉴스 수집", "value": f"{r.get('data_sources', {}).get('news_items')}건", "highlight": True},
                  {"label": "채권 센티", "value": f"{bond_s}점"},
                  {"label": "외환 센티", "value": f"{fx_s}점"},
                  {"label": "코인 센티", "value": f"{crypto_s}점"}],
                 "브래킷 값은 core/exit_planner가 실측 ATR·센티먼트로 재계산합니다. 섹터 점수는 헤드라인 키워드 기반(50=중립)입니다.")


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

    # ---- cross-asset technical block (bond / fx / crypto)
    cross = r.get("cross_asset_levels") or {}
    cross_rows = []
    for grp, gname in (("BOND", "채권"), ("FX", "외환"), ("CRYPTO", "코인")):
        for sym, v in cross.items():
            if v.get("group") != grp:
                continue
            cross_rows.append(
                f"• [{gname}] {sym} {_fmt(v.get('close'), 4)} | {v.get('trend')} | RSI14 {v.get('rsi14')} | "
                f"ATR {v.get('atr_pct')}% | 실현변동성 {v.get('realized_vol_20d_pct')}% | 20일 {v.get('ret_20d_pct')}% | "
                f"지지 {(v.get('support_pivot'))} / 저항 {(v.get('resistance_pivot'))}")
    cross_txt = "\n\n[크로스애셋 기술적 레벨 — 채권·환율·코인]\n" + ("\n".join(cross_rows) if cross_rows else "• 데이터 없음")
    cs_sum = r.get("cross_asset_summary") or {}
    cross_txt += (f"\n→ {(cs_sum.get('bonds') or {}).get('reading_ko')} / {(cs_sum.get('fx') or {}).get('reading_ko')} / "
                  f"{(cs_sum.get('crypto') or {}).get('reading_ko')}")
    content = ("실측 일봉으로 계산한 종목별 레벨입니다.\n\n" + "\n".join(rows) +
               (f"\n\n[월요일 개장 갭 시나리오] 금요일 종가 ${_fmt(gap.get('friday_close'))}, "
                f"선물 내재 갭 {_fmt(gap.get('futures_implied_gap_pct'))}% → 주경로 ${_fmt(gap.get('primary_open'))}, "
                f"대안 ${_fmt(gap.get('alternate_open'))}. ATR 밴드 ${_fmt((gap.get('atr_band') or [None])[0])}~${_fmt((gap.get('atr_band') or [None, None])[1])}. "
                f"{gap.get('plan','')}" if gap else "") + dip_txt +
               cross_txt +
               f"\n\n스몰캡 급등주 모델은 11-13 지시로 실전 배제 상태입니다.")
    cs = r.get("cross_asset_summary") or {}
    return _post(2, clock, "📈 레벨·진입신호 + 크로스애셋 기술적 (실측 OHLCV)",
                 f"[{clock['now_kst'][11:16]} 기술적 스캔] {', '.join(r.get('actionable_symbols') or ['진입 신호 없음'])} · "
                 f"BTC {(cs.get('crypto') or {}).get('btc', {}).get('trend')} / TLT {(cs.get('bonds') or {}).get('tlt', {}).get('trend')} / "
                 f"원/달러 {(cs.get('fx') or {}).get('usd_krw', {}).get('trend')}",
                 content,
                 [{"label": "SOXL ATR", "value": f"{lv.get('SOXL', {}).get('atr_pct')}%", "highlight": True},
                  {"label": "SOXL 돌파", "value": f"${lv.get('SOXL', {}).get('breakout_trigger')}", "highlight": True},
                  {"label": "BTC 추세", "value": (cs.get('crypto') or {}).get('btc', {}).get('trend')},
                  {"label": "TLT 추세", "value": (cs.get('bonds') or {}).get('tlt', {}).get('trend')}],
                 "개장 후 돌파 트리거 확인 시에만 진입합니다. 채권·환율·코인 레벨은 자산별 ATR 밴드와 주간 피벗으로 산출합니다.")


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
    lei_b = r.get("leading_indicators") or {}
    lei = lei_b.get("cube_lei_index")
    probit = lei_b.get("recession_probit") or {}
    comps = lei_b.get("components") or []
    credit = lei_b.get("credit") or {}
    bm = r.get("bond_market") or {}
    fx = r.get("fx_market") or {}
    cm = r.get("crypto_market") or {}
    y = bm.get("yields_pct") or {}
    cb = bm.get("curve_bp") or {}
    etfs = bm.get("etfs") or {}
    refs = lei_b.get("references") or []

    comp_lines = []
    for c in comps:
        mark = "✓" if c.get("available") else "×"
        comp_lines.append(
            f"  {mark} {c.get('name')} [{c.get('source')}] = {c.get('value')} (z {c.get('z')}) "
            f"· 가중 {c.get('weight')} · 기여 {c.get('contribution')}")
    ref_lines = [f"  - {x.get('cite')} → {x.get('used_for')}" for x in refs]
    bond_lines = []
    for t in ("TLT", "IEF", "SHY", "SGOV", "TIP", "HYG", "LQD"):
        e = etfs.get(t) or {}
        if not e:
            continue
        bond_lines.append(f"• {t} ${_fmt(e.get('last'))} ({_fmt(e.get('chg_pct'))}%) | 20일 {_fmt(e.get('ret_20d'))}% | "
                          f"63일 {_fmt(e.get('ret_63d'))}% | 듀레이션 {e.get('duration_years')}년")
    fx_lines = []
    for name, key in (("DXY", "dxy"), ("USD/KRW", "usd_krw"), ("USD/JPY", "usd_jpy"),
                      ("EUR/USD", "eur_usd"), ("USD/CNY", "usd_cny")):
        v = fx.get(key) or {}
        if v.get("last") is None:
            continue
        fx_lines.append(f"• {name} {_fmt(v.get('last'), 4)} ({_fmt(v.get('chg_pct'))}%)"
                        f" | 20일 {_fmt(v.get('ret_20d'))}%")
    cr = cm.get("btc") or {}
    ce = cm.get("eth") or {}
    crypto_lines = [f"• BTC ${_fmt(cr.get('last'), 0)} (24h {_fmt(cr.get('chg_pct'))}%, 7일 {_fmt(cr.get('ret_7d'))}%, 30일 {_fmt(cr.get('ret_30d'))}%)",
                    f"• ETH ${_fmt(ce.get('last'), 0)} (24h {_fmt(ce.get('chg_pct'))}%, 7일 {_fmt(ce.get('ret_7d'))}%)",
                    f"• SOL ${_fmt((cm.get('sol') or {}).get('last'), 2)} / XRP ${_fmt((cm.get('xrp') or {}).get('last'), 4)} | "
                    f"IBIT {(cm.get('ibit') or {}).get('last')} | COIN {(cm.get('coin') or {}).get('last')}",
                    f"• 주말(금→현재) 수익률 {_fmt(cm.get('weekend_return_pct'))}% · 리스크온 프록시 {_fmt(cm.get('risk_on_proxy_score'), 1)}/100"]

    content = (
        "【1】 경기선행지표 (논문 기반 실측 산출)\n"
        f"• CUBE-LEI 지수 {lei} (z {lei_b.get('cube_lei_z'):+}) · 0~100 점수 {lei_b.get('cube_lei_score')} · 국면 {lei_b.get('regime_ko')} "
        f"(3개월 변화 {lei_b.get('trend_3m_z_change')}z, 동일가중 z {lei_b.get('equal_weight_z')})\n"
        f"• 침체확률 P = Φ(-0.6045 - 0.8180×spread) = {probit.get('probability_10y3m')}% (10Y-3M {probit.get('spread_10y3m_bp')}bp) · "
        f"Estrella & Mishkin (1998)\n"
        f"• 역전 해소 후 {probit.get('months_since_curve_uninverted')}개월 — 문헌상 침체 신호 정점 구간(12개월 전후)과 겹칩니다\n"
        + "\n".join(comp_lines) + "\n"
        + "\n".join(ref_lines) + "\n\n"
        "【2】 미국 국채·채권 시장\n"
        f"• 커브: 3M {_fmt(y.get('3m'))}% / 2Y {_fmt(y.get('2y'))}% / 5Y {_fmt(y.get('5y'))}% / 10Y {_fmt(y.get('10y'))}% / 30Y {_fmt(y.get('30y'))}%\n"
        f"• 10Y-3M {cb.get('10y3m')}bp / 10Y-2Y {cb.get('10y2y')}bp / 30Y-10Y {cb.get('30y10y')}bp → {bm.get('curve_shape')}\n"
        + "\n".join(bond_lines) + "\n"
        f"• 크레딧: HY OAS {credit.get('hy_oas_bp')}bp (z {credit.get('hy_oas_z_5y')}, {credit.get('level')}) · "
        f"HYG-IEF 63일 상대 {credit.get('hyg_ief_rel_63d_pct')}%p · GZ 프록시 z {credit.get('gz_proxy_z')}\n"
        f"• 금리 10bp 상승 시 가격 영향: TLT {_fmt((bm.get('duration_pnl_per_10bp_pct') or {}).get('TLT'))}% / "
        f"IEF {_fmt((bm.get('duration_pnl_per_10bp_pct') or {}).get('IEF'))}% / SHY {_fmt((bm.get('duration_pnl_per_10bp_pct') or {}).get('SHY'))}%\n\n"
        "【3】 외환 시장\n" + "\n".join(fx_lines) + f"\n→ {fx.get('dollar_regime')} / {fx.get('krw_regime')}\n\n"
        "【4】 코인 시장 (24/7 리스크온 프록시)\n" + "\n".join(crypto_lines) + "\n\n"
        f"[리크 스오프 트리거 {len(r.get('risk_off_triggers', []))}개] "
        + ("; ".join(r.get("risk_off_triggers", [])) if r.get("risk_off_triggers") else "없음") + "\n"
        f"[다른 에이전트 전달값] CUBE-LEI {lei} · 침체확률 {probit.get('probability_used')}% · 신용 {credit.get('level')} · "
        f"레버리지 상한 계수 {(r.get('handoff_to_agents') or {}).get('max_gross_exposure_factor')} · "
        f"파킹 {(r.get('handoff_to_agents') or {}).get('parking_preference')}"
    )
    return _post(4, clock, "🌐 4대 영역 통합 보고 — 선행지표·채권·환율·코인 (논문 기반 실측)",
                 f"[{clock['now_kst'][11:16]} 종합] CUBE-LEI {lei} ({lei_b.get('regime_ko')}) · 침체확률 {probit.get('probability_used')}% · "
                 f"10Y {_fmt(y.get('10y'))}% · HY OAS {credit.get('hy_oas_bp')}bp · 원/달러 {_fmt((fx.get('usd_krw') or {}).get('last'))} · "
                 f"BTC {_fmt((cr.get('last') or 0) / 1000, 1)}K",
                 content,
                 [{"label": "CUBE-LEI", "value": f"{lei} ({lei_b.get('regime_ko')})", "highlight": True},
                  {"label": "침체확률", "value": f"{probit.get('probability_used')}%", "highlight": True},
                  {"label": "10Y-3M", "value": f"{cb.get('10y3m')}bp"},
                  {"label": "HY OAS", "value": f"{credit.get('hy_oas_bp')}bp"}],
                 "선행지표는 사이징·방어 계수로만 반영합니다(백테스트상 바이너리 청산은 CAGR·Sharpe 악화 — Agent 7 보고 참조).")


def post_agent5(r: Dict[str, Any], clock) -> Dict[str, Any]:
    chains = r.get("chains", {})
    cf = r.get("credit_flow") or {}
    df = r.get("duration_flow") or {}
    cf2 = r.get("crypto_institutional_flow") or {}
    rows = []
    for sym, c in chains.items():
        rows.append(f"• {sym} ({c.get('expiry')}): Put/Call OI {c.get('put_call_oi')} · 거래량비 {c.get('put_call_volume')} · "
                    f"감마월 ${_fmt(c.get('gamma_wall_strike'))} · 맥스페인 ${_fmt(c.get('max_pain_strike'))}")
    content = ("실제 옵션 체인 미결제약정 기준입니다. 소셜(레딧·X) 데이터는 연동되어 있지 않아 수치를 만들지 않습니다.\n\n"
               + ("\n".join(rows) if rows else "체인 조회 실패 — 다음 주기 재시도") +
               "\n\n[크레딧 자금흐름 — HYG/LQD · HY OAS]\n" +
               f"• HYG 20일 {_fmt(cf.get('hyg_ret_20d_pct'))}% vs LQD {_fmt(cf.get('lqd_ret_20d_pct'))}% "
               f"(상대 {_fmt(cf.get('hyg_vs_lqd_20d_pp'))}%p, HYG RVOL {cf.get('hyg_rvol')})\n"
               f"• HY OAS {cf.get('hy_oas_bp')}bp (z {cf.get('hy_oas_z_5y')}, {cf.get('credit_level')}) · GZ 프록시 z {cf.get('gz_proxy_z')}\n"
               f"• 판단: {cf.get('interpretation')}\n\n"
               "[듀레이션 자금흐름 — TLT vs IEF]\n"
               f"• TLT 20일 {_fmt(df.get('tlt_ret_20d_pct'))}% / IEF {_fmt(df.get('ief_ret_20d_pct'))}% · TLT RVOL {df.get('tlt_rvol')} · ATR {df.get('tlt_atr_pct')}%\n"
               f"• 판단: {df.get('long_duration_demand')}\n\n"
               "[기관 크립토 흐름 — IBIT/BTC]\n"
               f"• IBIT ${cf2.get('ibit_last')} (20일 {_fmt(cf2.get('ibit_ret_20d_pct'))}%, RVOL {cf2.get('ibit_rvol')}) · "
               f"COIN ${cf2.get('coin_last')} ({_fmt(cf2.get('coin_chg_pct'))}%)\n"
               f"• IBIT - BTC 20일 차이 {_fmt(cf2.get('ibit_minus_btc_pp'))}%p → {cf2.get('institutional_demand')}\n"
               f"• 주의: {cf2.get('note')}\n\n"
               f"데이터 시점: {r.get('data_as_of')} ({r.get('stale_note')})")
    return _post(5, clock, "🐋 기관 포지셔닝 (옵션 + 크레딧 + 듀레이션 + 크립토)",
                 f"[{clock['now_kst'][11:16]} 기관] QQQ P/C {r.get('qqq_put_call_oi')} → {r.get('institutional_bias')} · "
                 f"HY OAS {cf.get('hy_oas_bp')}bp({cf.get('credit_level')}) · TLT {df.get('long_duration_demand','—').split(' (')[0]} · "
                 f"IBIT {cf2.get('institutional_demand','—').split(' (')[0]}",
                 content,
                 [{"label": "QQQ P/C OI", "value": r.get("qqq_put_call_oi"), "highlight": True},
                  {"label": "HY OAS", "value": f"{cf.get('hy_oas_bp')}bp"},
                  {"label": "TLT 20일", "value": f"{_fmt(df.get('tlt_ret_20d_pct'))}%"},
                  {"label": "IBIT-BTC", "value": f"{_fmt(cf2.get('ibit_minus_btc_pp'))}%p"}],
                 "감마월 하회 시 변동성 확대 구간이므로 진입을 분할합니다. 크레딧·듀레이션·크립토 흐름은 20일 실측 모멘텀 기준입니다.")


def post_agent6(r: Dict[str, Any], clock) -> Dict[str, Any]:
    wb = r.get("weekend_playbook")
    rows = [f"• {s['id']} {s['name']}: {s['validated']['verdict']} (PF {s['validated']['PF']})" for s in r.get("scenarios", [])]
    wtxt = ""
    if wb:
        wtxt = ("\n\n[월요일 개장 플레이북] 금요일 종가 " + _fmt(wb.get("friday_close")) +
                f", 선물 내재 갭 {_fmt(wb.get('futures_implied_gap_pct'))}%\n" +
                "\n".join(f"• {c['case']}: 기준가 ${_fmt(c['open_ref'])} — {c['play']} [{c['bracket']}]" for c in wb["scenarios"]))
    lf = r.get("leading_indicator_filter") or {}
    adj = lf.get("probability_adjustment") or {}
    pri = {s["id"]: s.get("prior_probability_pct") for s in r.get("scenarios", [])}
    prob_rows = [f"• {sid}: 사전 {pri.get(sid)}% → 국면조정 {v}%" for sid, v in adj.items()]
    lf_txt = ("\n\n[선행지표 국면 반영 — CUBE-LEI]\n"
              f"• CUBE-LEI {lf.get('cube_lei_index')} · 국면 {lf.get('regime_ko')} · 침체확률 {lf.get('recession_probability_pct')}% · "
              f"신용 {lf.get('credit_level')}\n"
              f"• 레버리지 상한 계수 {lf.get('leverage_cap_factor')} · 종목당 상한 {lf.get('position_cap_pct')}% "
              f"({lf.get('position_cap_formula')})\n"
              f"• 국면 가족 배수: {lf.get('regime_family_multipliers')}\n"
              f"• 시나리오 확률 재배분(모델 가정 기반):\n" + "\n".join(prob_rows))
    content = (f"10개 시나리오를 실측 ATR(SOXL {r.get('atr_basis', {}).get('SOXL_atr_pct')}%, "
               f"TQQQ {r.get('atr_basis', {}).get('TQQQ_atr_pct')}%) 기준으로 재정규화했습니다.\n\n" + "\n".join(rows) + wtxt + lf_txt)
    return _post(6, clock, "📐 ATR 재정규화 시나리오 + 선행지표 국면 반영" + (" + 주말 플레이북" if wb else ""),
                 f"[{clock['now_kst'][11:16]} 시나리오] 유효 전략 {', '.join(r.get('approved_now', [])) or '없음'}" + (" · 월요일 3개 갭 시나리오" if wb else "") + f" · LEI {lf.get('regime', '—')} → 레버리지 상한 {lf.get('leverage_cap_factor')}",
                 content,
                 [{"label": "국면", "value": lf.get("regime", "—"), "highlight": True},
                  {"label": "레버리지 계수", "value": f"{lf.get('leverage_cap_factor')}"},
                  {"label": "종목 상한", "value": f"{lf.get('position_cap_pct')}%"},
                  {"label": "침체확률", "value": f"{lf.get('recession_probability_pct')}%"}],
                 "사전확률은 설계 가정이며 국면 배수만 적용해 재정규화합니다. 시나리오는 방향 판단이 아니라 브래킷과 트리거로만 사용합니다.")


def post_agent7(r: Dict[str, Any], clock) -> Dict[str, Any]:
    rows = []
    for s in r.get("backtest_results", []):
        m = s.get("metrics") or {}
        rows.append(f"• {s['name']}: {m.get('total_return_pct')}% · PF {m.get('profit_factor')} · 승률 {m.get('win_rate_pct')}% "
                    f"· Sharpe {m.get('sharpe_ratio')} · MDD {m.get('mdd_pct')}% · {m.get('total_trades')}회 → {s['status']}")
    ch = r.get("bear_engine_choice") or {}
    lf = r.get("lei_filter_backtest") or {}
    frows = []
    for row in lf.get("table", []):
        frows.append(f"• {row['filter']}: CAGR {row['cagr_pct']}% · MDD {row['mdd_pct']}% (무필터 대비 "
                     f"{row['mdd_delta_pp_vs_unfiltered']:+}%p) · Sharpe {row['sharpe']} "
                     f"({row['sharpe_delta_vs_unfiltered']:+}) · 시장노출 {row['time_in_market_pct']}% · 스위치 {row['switches']}회")
    base = lf.get("unfiltered_baseline") or {}
    lf_txt = ("\n\n[선행지표·장단기금리차 필터 방어 효과 검증 — 5년 시뮬]\n"
              f"• 기간 {((lf.get('window') or {}).get('start'))}~{((lf.get('window') or {}).get('end'))} · {lf.get('friction')}\n"
              f"• 무필터 SOXL 기준: CAGR {base.get('cagr_pct')}% · MDD {base.get('mdd_pct')}% · Sharpe {base.get('sharpe')}\n"
              + "\n".join(frows)
              + f"\n• 필터 조건: {lf.get('filter_state_days')}"
              + f"\n• 결론: {lf.get('verdict')}") if frows else ""
    content = ("60분봉 경로 기준 시뮬레이션(왕복 0.24% 마찰, 손절 우선 체결 가정)입니다. 실전 원장과 분리된 수치입니다.\n\n"
               + "\n".join(rows)
               + (f"\n\n[숏 엔진] VIX ≥ {ch.get('vix_trigger')} + 확인 {ch.get('confirmations_required')}개 조건이 "
                  f"Calmar 기준 최적으로 나타났습니다." if ch else "")
               + lf_txt
               + f"\n\n보고서 생성: {r.get('report_generated_at_kst')} ({r.get('report_age_hours')}시간 전)")
    return _post(7, clock, "🧪 백테스트 검증 (시간봉 경로 + 선행지표 필터 방어효과)",
                 f"[{clock['now_kst'][11:16]} 검증] {r.get('key_takeaway','')[:140]}",
                 content,
                 [{"label": "현행 장중", "value": f"{(r.get('backtest_results') or [{}])[0].get('metrics', {}).get('total_return_pct')}%"},
                  {"label": "DIP_SWING", "value": f"{(r.get('backtest_results') or [{}, {}, {}])[2].get('metrics', {}).get('total_return_pct')}%", "highlight": True},
                  {"label": "복합필터 MDD", "value": f"{next((x['mdd_pct'] for x in lf.get('table', []) if x['filter'] == 'F4_COMPOSITE_LEI'), '—')}%"},
                  {"label": "복합필터 Sharpe", "value": f"{next((x['sharpe'] for x in lf.get('table', []) if x['filter'] == 'F4_COMPOSITE_LEI'), '—')}"}],
                 "백테스트 수치는 원장에 반영하지 않습니다. 필터 검증은 방어 효과 실측이며 전략 성과가 아닙니다.")


def post_agent8(r: Dict[str, Any], clock) -> Dict[str, Any]:
    ap = r.get("profitable_scenarios", [])
    rj = r.get("rejected_scenarios", [])
    rows = [f"• 승인 {a['id']} (PF {a['pf']}, Sharpe {a['sharpe']}, MDD {a['mdd_pct']}%) — {a.get('alpha_source','')}" for a in ap]
    rej = [f"• 폐기 {x['id']}: {x.get('reject_reason')}" for x in rj]
    fx = r.get("fx_exposure") or {}
    sens = fx.get("sensitivity") or {}
    vol = fx.get("realized_vol") or {}
    sgov = r.get("sgov_vs_cash") or {}
    gap = r.get("crypto_weekend_gap_risk") or {}
    cross_txt = (
        "\n\n[환노출 — USD/KRW]\n"
        f"• 환율 {sens.get('fx_rate')}원 기준 평가금 {_fmt(sens.get('equity_krw'), 0)}원 (${_fmt(sens.get('equity_usd'))})\n"
        f"• 10원 변동 = {_fmt(sens.get('per_10won_krw'), 0)}원 (평가금의 {sens.get('per_10won_pct_of_equity')}%) · "
        f"1% 변동 = {_fmt(sens.get('per_1pct_krw'), 0)}원 · 일간 95% VaR {_fmt(fx.get('daily_var_95_krw'), 0)}원\n"
        f"• 실측 환율 변동성: 일간 {vol.get('daily_sigma_pct')}% / 연환산 {vol.get('annualized_pct')}% "
        f"(최대 1일 {vol.get('max_1d_move_pct')}%) · 국면 {fx.get('regime')}\n"
        f"• {fx.get('hedge_note')}\n\n"
        "[SGOV 단기채 vs 현금 기회비용]\n"
        f"• 3M T-bill 쿠폰등가 {sgov.get('annual_yield_pct')}% (보수 차감 {sgov.get('annual_yield_net_pct')}%) — "
        f"현금 ${_fmt(sgov.get('cash_usd'))}\n"
        f"• 연 이자 ${_fmt(sgov.get('annual_interest_usd'))} / 월 ${_fmt(sgov.get('monthly_interest_usd'))} "
        f"({_fmt(sgov.get('opportunity_cost_if_cash_sits_idle_krw_per_month'), 0)}원) / 연 "
        f"{_fmt(sgov.get('annual_interest_krw'), 0)}원\n"
        f"• 근거: {sgov.get('yield_basis')} · {sgov.get('net_of_fee_note')}\n\n"
        "[코인 주말 변동성 → 월요일 나스닥 갭 리스크]\n"
        + (f"• BTC 주말 {gap.get('weekend_btc_ret_now_pct')}% → 월요일 QQQ 시가 갭 추정 {gap.get('implied_monday_qqq_gap_pct')}%\n"
           f"• 회귀: β {gap.get('beta_monday_gap_per_1pct_weekend_btc')} (BTC 주말 1%당 갭 %p) · R² {gap.get('r_squared')} · "
           f"상관 {gap.get('correlation')} · 표본 {gap.get('sample_weeks')}주 · 관측 최대 갭 {gap.get('observed_max_gap_pct')}%\n"
           f"• {gap.get('note')}" if gap.get("available") else f"• {gap.get('note')}")
    )
    content = (f"승인 기준: PF ≥ {r['criteria']['profit_factor_min']}, Sharpe ≥ {r['criteria']['sharpe_min']}, "
               f"MDD ≤ {r['criteria']['max_dd_pct']}%, 거래 ≥ {r['criteria']['min_trades']}회.\n\n"
               + ("\n".join(rows) if rows else "• 승인 통과 전략 없음") + "\n\n[폐기]\n" + "\n".join(rej if rej else ["• 없음"])
               + cross_txt)
    return _post(8, clock, "🎯 크로스마켓 알파·리스크 판정 (환노출·단기채 캐리·코인 갭)",
                 f"[{clock['now_kst'][11:16]} 알파] 승인 {len(ap)}개 / 폐기 {len(rj)}개 · 실행 모드 {r.get('recommended_execution_mode')} · "
                 f"환노출 10원당 {_fmt(sens.get('per_10won_krw'), 0)}원 · SGOV {sgov.get('annual_yield_pct')}%/년",
                 content,
                 [{"label": "승인", "value": f"{len(ap)}개", "highlight": True},
                  {"label": "SGOV 캐리", "value": f"{sgov.get('annual_yield_pct')}%/년", "highlight": True},
                  {"label": "10원당 환영향", "value": f"{_fmt(sens.get('per_10won_krw'), 0)}원"},
                  {"label": "코인→갭 β", "value": f"{gap.get('beta_monday_gap_per_1pct_weekend_btc')}"}],
                 r.get("approval_note", "") + " 환율·캐리·코인 갭은 실측치이며 주문에는 반영되지 않습니다(참고 지표).")


def post_agent9(r: Dict[str, Any], clock) -> Dict[str, Any]:
    w = r.get("target_portfolio_weights", {})
    alloc = r.get("target_alloc_usd", {})
    axes = r.get("five_axis") or {}
    ax_labels = {"equity": "주식", "bond": "채권", "fx": "외환", "crypto": "코인", "cycle": "사이클"}
    ax_rows = []
    for key, lbl in ax_labels.items():
        a = axes.get(key) or {}
        comp = a.get("components") or {}
        ax_rows.append(f"• {lbl}축 {a.get('score'):+} — " + " · ".join(f"{k} {v:+}" for k, v in comp.items()))
    gate = r.get("bear_engine_gate") or {}
    content = (f"레짐 {r.get('regime')} / VIX {r.get('vix')}. {r.get('regime_note')}\n\n"
               "[5축 신호 — -100(위험) ~ +100(우호)]\n" + "\n".join(ax_rows) +
               f"\n→ 5축 복합 {r.get('composite_score'):+} → 태세 {r.get('market_stance')} ({r.get('stance_rule')})\n"
               f"→ 태세 목표: 주식 {((r.get('stance_target') or {}).get('equity_gross') or 0)*100:.0f}% / "
               f"채권·파킹 {((r.get('stance_target') or {}).get('bond_parking') or 0)*100:.0f}% · "
               f"CUBE-LEI 레버리지 계수 {r.get('lei_leverage_factor')}\n\n"
               "[목표 비중]\n" +
               "\n".join(f"• {k}: {v*100:.0f}% (${_fmt(alloc.get(k))} / {_fmt(r.get('target_alloc_krw', {}).get(k), 0)}원)"
                         for k, v in w.items() if v) +
               f"\n\n[BEAR ALPHA 게이트 — 보존] VIX ≥ {gate.get('vix_trigger')} + 확인 {gate.get('confirmations_required')}개 · "
               f"현재 확인 {len(gate.get('confirmations_now') or [])}개: {'; '.join(gate.get('confirmations_now') or []) or '없음'}"
               f"\n\n승인 전략: {', '.join(r.get('approved_strategies', [])) or '없음'}")
    return _post(9, clock, "🧬 5축 앙상블 → 포지션 태세",
                 f"[{clock['now_kst'][11:16]} 태세] {r.get('market_stance')} · 5축 복합 {r.get('composite_score'):+} · " +
                 " / ".join(f"{k} {v*100:.0f}%" for k, v in w.items() if v),
                 content,
                 [{"label": "태세", "value": r.get("market_stance"), "highlight": True},
                  {"label": "5축 복합", "value": f"{r.get('composite_score'):+}"},
                  {"label": "SOXL", "value": f"{(w.get('SOXL') or 0)*100:.0f}%"},
                  {"label": "SGOV", "value": f"{(w.get('SGOV') or 0)*100:.0f}%"}],
                 "목표 비중은 상한이며 실제 주문은 개장 후 신호로 확정합니다. BEAR_HEDGE는 VIX 24 + 확인 2개에서만 발동합니다.")


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
    lic = r.get("leading_indicator_controls") or {}
    park = r.get("parking_plan") or {}
    fx = r.get("fx_commentary") or {}
    wk = r.get("crypto_weekend_alert") or {}
    pres = r.get("preserved_controls") or {}
    controls_txt = (
        "\n\n[선행지표 기반 사이징 — CUBE-LEI]\n"
        f"• 국면 {lic.get('regime_ko')} (LEI {lic.get('cube_lei_index')}, 침체확률 {lic.get('recession_probability_pct')}%, "
        f"신용 {lic.get('credit_level')})\n"
        f"• 레버리지 계수 {lic.get('leverage_cap_factor')} → 종목 상한 {lic.get('position_cap_pct')}% · "
        f"거래당 리스크 {lic.get('risk_per_trade_pct_effective')}%\n"
        f"• 적용: {lic.get('applied_to')} · 규칙: {lic.get('sizing_rule')}\n\n"
        "[단기채 파킹 제안 — SGOV/SHY]\n"
        f"• {park.get('proposal')}\n"
        f"• 듀레이션 스탠스: {park.get('bond_duration_stance')}\n"
        f"• {park.get('note')}\n\n"
        "[환율 코멘트 — USD/KRW]\n"
        f"• 현재 {fx.get('usd_krw')}원 ({fx.get('regime')}) · 10원당 계좌 영향 {_fmt(fx.get('per_10won_krw'), 0)}원 · "
        f"일간 95% VaR {_fmt(fx.get('daily_var_95_krw'), 0)}원\n"
        f"• {fx.get('entry_commentary')}\n\n"
        "[코인 주말 급변동 경보]\n"
        f"• BTC 주말 {_fmt(wk.get('btc_weekend_return_pct'))}% → 월요일 나스닥 갭 추정 {_fmt(wk.get('implied_monday_qqq_gap_pct'))}% "
        f"(발동 {'예' if wk.get('triggered') else '아니오'})\n"
        f"• 조치: {wk.get('action')}\n\n"
        "[보존 통제 — 변경 없음]\n"
        f"• DIP_SWING 승인: {pres.get('dip_swing_approval')}\n"
        f"• NVDA: {pres.get('nvda_levels_locked')}\n"
        f"• BEAR ALPHA: {pres.get('bear_alpha_gate')}\n"
        f"• {pres.get('intraday_rotation')}"
    )
    content = (
        f"원장 정합성 {r.get('consistency_check', {}).get('status')} "
        f"(이슈 {len(r.get('consistency_check', {}).get('issues', []))}건, 거래일 플래그 "
        f"{len(r.get('consistency_check', {}).get('fixed_trading_day_flags', []))}건 수정).\n"
        f"실현 +${_fmt(led.get('realized_pnl_usd'))} / 미실현 ${_fmt(led.get('unrealized_pnl_usd'))} / "
        f"평가 ${_fmt(led.get('equity_usd'))} ({_fmt(led.get('equity_krw'), 0)}원)\n"
        f"현금 ${_fmt(led.get('cash_usd'))} / {_fmt(led.get('cash_krw'), 0)}원 ({led.get('cash_pct')}%) · 환율 {led.get('fx_rate')}원 "
        f"({led.get('fx_source')})\n\n[브래킷 재산정]\n" + ("\n".join(rows) if rows else "• 활성 브래킷 없음") +
        "\n\n[개장 대기 계획]\n" + "\n".join(ptxt) + swing_txt + controls_txt +
        f"\n\n다음 개장 {r.get('next_open_kst')} · 상태 {r.get('execution_status')}"
    )
    return _post(10, clock, "⚡ 집행관 — 원장·브래킷·선행지표 사이징·파킹·환율",
                 f"[{clock['now_kst'][11:16]} 집행] 현금 {_fmt(led.get('cash_pct'))}% (${_fmt(led.get('cash_usd'))}) · "
                 f"LEI 상한 {lic.get('position_cap_pct')}% · SGOV 파킹 월 ${lic and _fmt((park.get('sgov_carry') or {}).get('monthly_interest_usd'))} · "
                 f"태세 {r.get('market_stance')} · 계획 {len(plan)}건",
                 content,
                 [{"label": "평가자산", "value": f"${_fmt(led.get('equity_usd'))}", "highlight": True},
                  {"label": "종목 상한", "value": f"{lic.get('position_cap_pct')}%", "highlight": True},
                  {"label": "SGOV 월이자", "value": f"${_fmt((park.get('sgov_carry') or {}).get('monthly_interest_usd'))}"},
                  {"label": "코인 경보", "value": "발동" if wk.get("triggered") else "없음"}],
                 "주문은 개장 세션에서 신호 확인 후 집행합니다. DIP_SWING 승인·NVDA 레벨($173/$150)·BEAR ALPHA 게이트는 그대로 보존됩니다.")


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

    # ---- cross-market / leading-indicator panel payload (for cube.html & feed.html)
    a4 = agents.get(4, {})
    li = a4.get("leading_indicators") or {}
    a9 = agents.get(9, {})
    a8 = agents.get(8, {})
    cross_market = {
        "engine": li.get("engine"),
        "generated_at": clock["now_kst"],
        "cube_lei": {
            "index": li.get("cube_lei_index"), "score": li.get("cube_lei_score"), "z": li.get("cube_lei_z"),
            "regime": li.get("regime"), "regime_ko": li.get("regime_ko"),
            "trend_3m": li.get("trend_3m_z_change"), "equal_weight_z": li.get("equal_weight_z"),
            "monthly_history": li.get("monthly_history") or [],
        },
        "recession_probit": li.get("recession_probit") or {},
        "components": li.get("components") or [],
        "references": li.get("references") or [],
        "methodology": li.get("methodology") or {},
        "bonds": a4.get("bond_market") or {},
        "fx": a4.get("fx_market") or {},
        "crypto": a4.get("crypto_market") or {},
        "credit": li.get("credit") or {},
        "market_stance": a9.get("market_stance"),
        "five_axis": a9.get("five_axis") or {},
        "composite_score": a9.get("composite_score"),
        "fx_exposure": a8.get("fx_exposure") or {},
        "sgov_vs_cash": a8.get("sgov_vs_cash") or {},
        "crypto_weekend_gap_risk": a8.get("crypto_weekend_gap_risk") or {},
        "agent_adjustments": [
            {"agent": 1, "change": "채권/연준·외환·크립토 섹터 센티먼트 분리 보고"},
            {"agent": 2, "change": "TLT·IEF·DXY·원달러·엔달러·BTC·ETH 기술적 레벨 산출"},
            {"agent": 4, "change": "CUBE-LEI·침체확률·국채 커브·크레딧·환율·코인 4대 영역 통합 보고"},
            {"agent": 5, "change": "HYG/LQD 크레딧·TLT 듀레이션·IBIT/BTC 기관 흐름 반영"},
            {"agent": 6, "change": f"국면 {li.get('regime')} → 시나리오 확률 재배분·레버리지 상한 계수 적용"},
            {"agent": 7, "change": "선행지표·금리차 필터의 MDD/Sharpe 방어효과 5년 시뮬 검증"},
            {"agent": 8, "change": "환노출·SGOV 캐리·코인 주말→월요일 갭 리스크 평가"},
            {"agent": 9, "change": f"5축 융합 → 태세 {a9.get('market_stance')} 결정"},
            {"agent": 10, "change": "국면별 사이징·종목 상한·SGOV 파킹 제안·환율 코멘트·코인 주말 경보"},
        ],
    }

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
            "us_10y_yield": gm("^TNX"), "tlt": gm("TLT"), "btc": gm("BTC-USD"), "eth": gm("ETH-USD"),
            "hyg": gm("HYG"), "ief": gm("IEF"), "sgov": gm("SGOV"),
        },
        "cross_market": cross_market,
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
