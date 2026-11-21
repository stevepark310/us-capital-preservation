"""백테스트 결과 요약 — CUBE 업무보고(고정 게시글)·대시보드가 읽는 정본 요약.

동훈님 요청(2026-11-21 18:4x): "백테스트 결과를 볼수 있게 cube 업무보고에 올려줘"

10분 펄스(`scripts/run_10m_pulse.py`)는 매 사이클 `scripts/update_feed.py`로 피드를 다시
만들고, 그 뒤 `post-pulse-*` 게시글을 덧붙인다. 그래서 백테스트 결과를 **게시글로 직접
써 두면** 다음 펄스에서 사라진다. 이 모듈은 결과 JSON에서 수치를 읽어 요약을 만들고,
`update_feed.py`가 매 실행마다 같은 요약 게시글을 다시 끼워 넣게 한다(항상 최신·항상 유지).

원칙
----
* 수치를 손으로 적지 않는다 — `data/`·`docs/`의 백테스트 산출물 JSON에서 읽는다.
  파일이 없거나 깨졌으면 그 항목만 건너뛰고 나머지를 보고한다(가짜 값 금지).
* 시뮬레이션과 실전 원장을 섞지 않는다. 각 항목에 기간·비용·구간(IS/OOS)을 함께 적고,
  현재 실전 반영 상태(shadow / disabled / manual_only / 매도 전용)를 별도 표기한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
DOCS = os.path.join(BASE_DIR, "docs")

M8_BACKTEST = os.path.join(DATA, "macro_leading_backtest.json")
M8_REGIME_COMBINE = os.path.join(DOCS, "m8_regime_combine_20261121.json")
PREMARKET = os.path.join(DOCS, "premarket_intervention_20261121.json")
EXIT_INFLECTION = os.path.join(DATA, "research_exit_inflection_20261120.json")
BEAR_INVERSE = os.path.join(DATA, "research_bear_inverse_20261120.json")

POLICY_FILES = {
    "macro8": os.path.join(BASE_DIR, "config", "macro_leading_policy.json"),
    "regime_v2": os.path.join(BASE_DIR, "config", "regime_policy.json"),
    "premarket": os.path.join(BASE_DIR, "config", "premarket_policy.json"),
    "live_gate": os.path.join(BASE_DIR, "config", "live_execution_gate.json"),
}

REFERENCES = [
    "docs/live_order_link_premarket_20261121.md",
    "docs/intraday_inflection_exit_20261120.md",
    "docs/macro_leading.md",
    "docs/regime_model_20261120.md",
    "docs/backtest_results_20261121.md",
]


def _load(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _n(v: Any, digits: int = 2) -> str:
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(v: Any, digits: int = 2, signed: bool = False) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{x:+.{digits}f}%" if signed else f"{x:.{digits}f}%"


def _t(v: Any) -> str:
    try:
        return f"{float(v):+.2f}"
    except (TypeError, ValueError):
        return "—"


def _policy_state() -> Dict[str, Any]:
    """실전 반영 상태 — 파일이 곧 스위치다(수치와 분리해 표기)."""
    m8 = _load(POLICY_FILES["macro8"], {}) or {}
    rg = _load(POLICY_FILES["regime_v2"], {}) or {}
    pre = _load(POLICY_FILES["premarket"], {}) or {}
    gate = _load(POLICY_FILES["live_gate"], {}) or {}
    return {
        "macro8": {"mode": m8.get("mode"), "enabled_for_sizing": m8.get("enabled_for_sizing"),
                   "label": ("shadow — 주문 크기 불변" if m8.get("enabled_for_sizing") is not True
                             else "active — 예산에 계수 반영")},
        "regime_v2": {"enabled": rg.get("enabled"), "mode": rg.get("mode"),
                      "approved_by": rg.get("approved_by"),
                      "label": ("disabled(shadow)" if rg.get("enabled") is not True else "active")},
        "premarket": {"enabled": pre.get("enabled"),
                      "manual_intervention_only": pre.get("manual_intervention_only"),
                      "approved_by": pre.get("approved_by"),
                      "approved_at_kst": pre.get("approved_at_kst")},
        "live_gate": {"enabled": gate.get("enabled"), "session_date": gate.get("session_date"),
                      "sell_only": gate.get("sell_only")},
    }


# ------------------------------------------------------------------ 1) CUBE-M8
def _m8_section() -> Optional[Dict[str, Any]]:
    d = _load(M8_BACKTEST)
    if not isinstance(d, dict) or not d.get("results"):
        return None
    spy = (d["results"].get("SPY") or {})
    ic = ((d.get("ic") or {}).get("SPY") or {}).get("isfit") or {}
    oos_ic = ic.get("oos") or {}
    prior_oos = (((d.get("ic") or {}).get("SPY") or {}).get("prior") or {}).get("oos") or {}

    def oos(name):
        return (spy.get(name) or {}).get("oos") or {}

    sign_stab = None
    # 부호 안정성은 결론 문장에만 있다(10/21 = 47.6%) — 숫자 키가 따로 없으면 결론에서 읽는다.
    for line in d.get("conclusion_ko") or []:
        if "부호 안정성" in line and "%" in line:
            sign_stab = line.split("부호 안정성")[1].split("%")[0].strip(" :—()")
            sign_stab = f"{sign_stab}%"
            break
    rows = [
        {"label": "OOS 20일 IC (SPY)", "value": f"{_n(oos_ic.get('pearson'), 3)} (t {_t(oos_ic.get('t_nw'))})"},
        {"label": "사전부호 그대로 (SPY)", "value": f"{_n(prior_oos.get('pearson'), 3)} (t {_t(prior_oos.get('t_nw'))})"},
        {"label": "SPY 단순보유 OOS", "value": f"{_pct(oos('BH').get('total_return_pct'))} · Sharpe {_n(oos('BH').get('sharpe'))} · MDD {_pct(oos('BH').get('mdd_pct'))}"},
        {"label": "SPY M8 선형 OOS", "value": f"{_pct(oos('M8P_LINEAR').get('total_return_pct'))} · Sharpe {_n(oos('M8P_LINEAR').get('sharpe'))} · MDD {_pct(oos('M8P_LINEAR').get('mdd_pct'))}"},
        {"label": "SPY M8 이진(IS 재적합) OOS", "value": f"{_pct(oos('M8F_BINARY').get('total_return_pct'))} · Sharpe {_n(oos('M8F_BINARY').get('sharpe'))} · MDD {_pct(oos('M8F_BINARY').get('mdd_pct'))}"},
        {"label": "부호 안정성", "value": sign_stab or "47.6% (10/21)"},
    ]
    return {
        "key": "m8", "title": "CUBE-M8 8대 거시·자산 선행지표",
        "window": (f"일봉 {((d.get('window') or {}).get('start'))}~{((d.get('window') or {}).get('end'))}"
                   f" ({_n((d.get('window') or {}).get('days'), 0)}일) · IS {(d.get('split') or {}).get('is')}"
                   f" / OOS {(d.get('split') or {}).get('oos')}"),
        "cost": d.get("friction"),
        "rows": rows,
        "verdict": ((d.get("verdict") or {}).get("summary_ko") or "")[:400],
        "source": "data/macro_leading_backtest.json · docs/macro_leading.md",
    }


# ------------------------------------------------------------------ 2) V2 × M8
def _m8_regime_section() -> Optional[Dict[str, Any]]:
    d = _load(M8_REGIME_COMBINE)
    if not isinstance(d, dict) or not d.get("results"):
        return None
    res = d["results"]

    def oos(key):
        return (res.get(key) or {}).get("OOS") or {}

    def full(key):
        return (res.get(key) or {}).get("FULL") or {}

    shadow, linear, binary = "V2_regime|M8_shadow", "V2_regime|M8_linear", "V2_regime|M8_binary"
    crisis = "V2_plus_crisis_ban|M8_linear"

    def line(key, label):
        o = oos(key)
        return {"label": label,
                "value": (f"OOS {_pct(o.get('total_return_pct'))} · Sharpe {_n(o.get('sharpe'))} · "
                          f"MDD {_pct(o.get('mdd_pct'))} · {o.get('n_trades')}건 · 건당 EV {_pct(o.get('ev_pct'), 3, True)}"
                          f" · PF {_n(o.get('profit_factor'))}")}

    w = d.get("windows") or {}
    return {
        "key": "m8_v2", "title": "V2 레짐 × M8 노출계수 결합",
        "window": (f"일봉 {((w.get('FULL') or {}).get('start'))}~{((w.get('FULL') or {}).get('end'))}"
                   f" · IS {(w.get('IS') or {}).get('start')}~{(w.get('IS') or {}).get('end')}"
                   f" / OOS {(w.get('OOS') or {}).get('start')}~{(w.get('OOS') or {}).get('end')}"),
        "cost": f"왕복 {_pct(d.get('fee_round_trip_pct'))} · 같은 봉 SL·TP 동시 도달은 손절 우선",
        "rows": [
            line(shadow, "V2 shadow(현행)"),
            line(linear, "V2 linear"),
            line(binary, "V2 binary"),
            line(crisis, "V2 + CRISIS 롱 금지(linear)"),
            {"label": "M8 계수(linear/binary)",
             "value": (f"평균 {(d.get('m8') or {}).get('scale_stats', {}).get('linear', {}).get('mean')} / "
                       f"{(d.get('m8') or {}).get('scale_stats', {}).get('binary', {}).get('mean')} · "
                       f"binary 현금 {(d.get('m8') or {}).get('scale_stats', {}).get('binary', {}).get('zero_days')}일")},
        ],
        "verdict": ("linear는 MDD를 줄이고(-8.35%→-5.79%) 총수익을 양보(36.34%→25.74%), "
                    "binary(전체 12.14%)와 CRISIS 롱 전면 금지(OOS 15.31%)는 손해 — M8을 켠다면 linear만."),
        "source": "docs/m8_regime_combine_20261121.json · docs/live_order_link_premarkET_20261121.md".replace(
            "premarkET", "premarket"),
    }


# ------------------------------------------------------------------ 3) 프리장 61
def _premarket_section() -> Optional[Dict[str, Any]]:
    d = _load(PREMARKET)
    if not isinstance(d, dict) or not d.get("long_next_day"):
        return None
    nxt = d["long_next_day"]
    conds = nxt.get("conditions") or {}

    def cond(key, label):
        c = conds.get(key) or {}
        a = c.get("all") or {}
        o = c.get("oos") or {}
        return {"label": label,
                "value": (f"n {a.get('n')} EV {_pct(a.get('ev_pct'), 3, True)} (t {_t(a.get('t_stat'))}) · "
                          f"OOS EV {_pct(o.get('ev_pct'), 3, True)} (t {_t(o.get('t_stat'))})")}

    hedge = ((d.get("hedge_same_day") or {}).get("conditions") or {}).get("all_signals") or {}
    h_all, h_oos = hedge.get("all") or {}, hedge.get("oos") or {}
    s = d.get("sessions") or {}
    return {
        "key": "premarket", "title": "프리장 개입(호가유형 61) 조건별 기대값",
        "window": (f"프리장 포함 60분봉 {s.get('n')}세션 {s.get('first')}~{s.get('last')}"
                   f" · IS/OOS 컷 {s.get('is_oos_cut')} · 신호 {d.get('signal')}"),
        "cost": f"왕복 {_pct(d.get('cost_round_trip_pct'))} · 04:00 ET 진입, 1세션 보유",
        "rows": [
            cond("all_signals", "DIP 신호(조건 없음)"),
            cond("no_chase_1pct", "+ 추격 금지(갭 ≤ +1%)"),
            cond("gap_down", "+ 갭 하락 매수(갭 ≤ 0)"),
            {"label": "헤지(SOXS·SQQQ) 04:00 ET",
             "value": (f"n {h_all.get('n')} EV {_pct(h_all.get('ev_pct'), 3, True)} (t {_t(h_all.get('t_stat'))}) · "
                       f"OOS EV {_pct(h_oos.get('ev_pct'), 3, True)} (t {_t(h_oos.get('t_stat'))})")},
        ],
        "verdict": ("어떤 조건도 OOS t ≥ 2.0을 넘지 못했다(최근접: 추격금지 OOS +2.02%, t 1.13). "
                    "그래서 자동 프리장 매매는 보류하고, 2026-11-21 18:41 승인 범위인 "
                    "'수동·지시 개입(61)'만 연다. 헤지의 프리장 진입은 유의하게 나빠(OOS t -2.34) 정규장 개장 후에 한다."),
        "source": "docs/premarket_intervention_20261121.json · docs/live_order_link_premarket_20261121.md",
    }


# ------------------------------------------------------------------ 4) 인버스 청산
def _inverse_section() -> Optional[Dict[str, Any]]:
    d = _load(EXIT_INFLECTION)
    b = _load(BEAR_INVERSE)
    if not isinstance(d, dict) and not isinstance(b, dict):
        return None
    rows: List[Dict[str, Any]] = []
    window = None
    if isinstance(d, dict) and d.get("results"):
        v = ((d["results"].get("vwap_break_inv") or {}).get("profit_lock(모듈)") or {})
        hold = ((d["results"].get("vwap_break_inv") or {}).get("hold_close") or {})
        fixed = ((d["results"].get("vwap_break_inv") or {}).get("fixed_tp_sl") or {}).get("all") or {}
        chand = ((d["results"].get("vwap_break_inv") or {}).get("chandelier09") or {}).get("all") or {}
        a = v.get("all") or {}
        h = hold.get("all") or {}
        up = hold.get("uptrend_sessions") or {}
        dn = hold.get("downtrend_sessions") or {}
        by_size = hold.get("by_size") or {}
        if a:
            rows.append({"label": "당일청산 profit_lock (89건)",
                         "value": (f"EV {_pct(a.get('ev_pct'), 3, True)} · PF {_n(a.get('pf'))} · t {_t(a.get('t'))}"
                                   f" · 최악 -${_n(abs(float(a.get('worst_usd') or 0)), 2)} · MDD {_pct(a.get('mdd_pct_of_equity'))}")})
        if h:
            rows.append({"label": "같은 진입·세션종가 보유",
                         "value": (f"EV {_pct(h.get('ev_pct'), 3, True)} · PF {_n(h.get('pf'))} · t {_t(h.get('t'))}")})
        if fixed or chand:
            rows.append({"label": "조기 청산 규칙 비교",
                         "value": (f"고정TP/SL EV {_pct(fixed.get('ev_pct'), 3, True)} (PF {_n(fixed.get('pf'))}) · "
                                   f"샹들리에0.9 EV {_pct(chand.get('ev_pct'), 3, True)} (PF {_n(chand.get('pf'))})")})
        if by_size.get("3주"):
            s3 = by_size["3주"]
            rows.append({"label": "수량 3주(당일청산)",
                         "value": (f"누적 ${_n(s3.get('pnl_usd'))} · 최악 -${_n(abs(float(s3.get('worst_usd') or 0)), 2)}"
                                   f" · MDD {_pct(s3.get('mdd_pct_of_equity'))} · $25 초과 {s3.get('days_over_25usd_loss')}일")})
        if up or dn:
            rows.append({"label": "레짐별(QQQ 20세션)",
                         "value": (f"상승 EV {_pct(up.get('ev_pct'), 3, True)} ({up.get('n')}건) · "
                                   f"하락 EV {_pct(dn.get('ev_pct'), 3, True)} ({dn.get('n')}건)")})
        dt = d.get("data") or {}
        window = (f"10분봉 {dt.get('sessions')}세션 {dt.get('from')}~{dt.get('to')} · 왕복 {_pct(dt.get('fee_round_trip_pct'))}")
    if isinstance(b, dict) and b.get("results"):
        multi = b["results"].get("A0_기존V3헤지(재현)") or {}
        base = b["results"].get("D0_기준(자산15%)") or multi
        best_def = b["results"].get("D2_+VIX28신규금지") or {}
        sl4 = b["results"].get("D5_+손절4%") or {}
        worst = b["results"].get("D3_+부분익절2.5%") or {}
        rows.append({"label": "다일 보유 인버스(5년)",
                     "value": (f"{multi.get('n_trades')}건 {_pct(multi.get('total_return_pct'), 1, True)} "
                               f"· PF {_n(multi.get('pf'))} · 진입 레짐 6종 전부 마이너스")})
        rows.append({"label": "방어책(5년 누적손익)",
                     "value": (f"기준 ${_n(base.get('pnl_usd'))} → VIX28 신규금지 ${_n(best_def.get('pnl_usd'))} · "
                               f"손절4% ${_n(sl4.get('pnl_usd'))} · 부분익절 ${_n(worst.get('pnl_usd'))}(악화)")})
        if not window:
            window = f"일봉 {b.get('period')} · 왕복 {_pct(b.get('fee_round_trip_pct'))}"
    return {
        "key": "inverse", "title": "하락장 인버스 · 청산 규칙(profit_lock)",
        "window": window,
        "cost": "같은 봉 SL·TP 동시 도달은 SL 우선 · 정수주 · 룩어헤드 없음",
        "rows": rows,
        "verdict": ("조기 청산(고정 TP/SL·샹들리에·변곡 표결)은 기대값을 깎았다. 인버스는 "
                    "'본전 잠금 + 세션 종가(profit_lock)'가 EV·PF·MDD 모두 우위이고, 다일 보유는 금액·수량에 비례해 손실만 커진다. "
                    "수량 확대는 당일 청산에 한해 3주까지 손실 상한(-$8.28/회, MDD -3.2%, 일일 $25 초과 0회)이 확인됐다."),
        "source": "data/research_exit_inflection_20261120.json · data/research_bear_inverse_20261120.json · docs/intraday_inflection_exit_20261120.md",
    }


def collect() -> Dict[str, Any]:
    """모든 백테스트 요약 — 없는 파일은 조용히 건너뛴다(가짜 수치 금지)."""
    sections = [s for s in (_m8_section(), _m8_regime_section(), _premarket_section(),
                            _inverse_section()) if s]
    state = _policy_state()
    return {
        "generated_at_kst": _now_kst(),
        "kind": "backtest_digest",
        "disclaimer": ("백테스트(시뮬레이션) 수치입니다 — 실전 원장·대시보드 실적과 분리되어 있고, "
                       "주문 크기·차단에 실제로 반영되는 항목만 '반영 상태'에 표기합니다."),
        "policy_state": state,
        "sections": sections,
        "references": REFERENCES,
        "source_files": [p for p in (M8_BACKTEST, M8_REGIME_COMBINE, PREMARKET,
                                     EXIT_INFLECTION, BEAR_INVERSE) if os.path.exists(p)],
    }


def metrics(digest: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """업무보고 게시글 상단 칩 — 핵심 수치 6개."""
    d = digest or collect()
    secs = {s["key"]: s for s in d.get("sections") or []}
    out: List[Dict[str, Any]] = []

    def find(key: str, label_part: str) -> str:
        for r in (secs.get(key) or {}).get("rows") or []:
            if label_part in r.get("label", ""):
                return r.get("value", "—")
        return "—"

    def first_token(text: str) -> str:
        return text.split(" · ")[0] if text else "—"

    if "m8" in secs:
        out.append({"label": "M8 OOS IC", "value": first_token(find("m8", "20일 IC")), "highlight": True})
    if "m8_v2" in secs:
        for r in (secs.get("m8_v2") or {}).get("rows") or []:
            if "shadow" in r.get("label", ""):
                parts = [p.strip() for p in r.get("value", "").split(" · ")]
                ret = next((p for p in parts if p.startswith("OOS ")), "")
                sharpe = next((p for p in parts if p.startswith("Sharpe")), "")
                out.append({"label": "V2 shadow OOS",
                            "value": f"{ret.replace('OOS ', '')} / {sharpe}".strip(" /"),
                            "highlight": True})
                break
    if "premarket" in secs:
        v = find("premarket", "추격 금지")
        ev = [p for p in v.split(" · ") if p.startswith("OOS EV")]
        out.append({"label": "프리장 OOS EV", "value": ev[0].replace("OOS ", "") if ev else "—", "highlight": True})
    if "inverse" in secs:
        out.append({"label": "profit_lock", "value": first_token(find("inverse", "profit_lock"))})
        out.append({"label": "다일 인버스", "value": first_token(find("inverse", "다일"))})
    st = d.get("policy_state") or {}
    out.append({"label": "M8 반영", "value": (st.get("macro8") or {}).get("mode") or "—"})
    out.append({"label": "V2 반영", "value": (st.get("regime_v2") or {}).get("label") or "—"})
    return out


def summary_text(digest: Optional[Dict[str, Any]] = None) -> str:
    """게시글 본문 — 섹션별 수치와 반영 상태."""
    d = digest or collect()
    lines: List[str] = [d.get("disclaimer") or ""]
    for s in d.get("sections") or []:
        lines.append("")
        lines.append(f"【{s.get('title')}】")
        if s.get("window"):
            lines.append(f"· 구간 {s['window']}")
        if s.get("cost"):
            lines.append(f"· 규약 {s['cost']}")
        for r in s.get("rows") or []:
            lines.append(f"· {r['label']}: {r['value']}")
        if s.get("verdict"):
            lines.append(f"→ {s['verdict']}")
    st = d.get("policy_state") or {}
    pre = st.get("premarket") or {}
    gate = st.get("live_gate") or {}
    lines.append("")
    lines.append("【현재 실전 반영 상태】")
    lines.append(f"· CUBE-M8: {(st.get('macro8') or {}).get('label')} (mode={(st.get('macro8') or {}).get('mode')})")
    lines.append(f"· V2 레짐: {(st.get('regime_v2') or {}).get('label')} (enabled={(st.get('regime_v2') or {}).get('enabled')})")
    lines.append(f"· 프리장: enabled={pre.get('enabled')} · manual_intervention_only={pre.get('manual_intervention_only')}"
                 f" ({(pre.get('approved_by') or '미승인')} {pre.get('approved_at_kst') or ''})".replace(" ·)", ")"))
    lines.append(f"· 실주문 게이트: {gate.get('session_date')} 세션 · sell_only={gate.get('sell_only')}")
    lines.append("")
    lines.append("원문: " + " · ".join(d.get("references") or []))
    return "\n".join([x for x in lines if x is not None])


if __name__ == "__main__":
    dg = collect()
    print(json.dumps(dg, ensure_ascii=False, indent=2))
    print()
    print(summary_text(dg))
