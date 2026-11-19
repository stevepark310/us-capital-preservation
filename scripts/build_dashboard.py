#!/usr/bin/env python3
"""
통합 대시보드 렌더러 — `index.html` 한 장.

동훈님 지시(2026-11-18 16:04): "혼재된 대시보드를 단일 통합 대시보드로 병합하고,
CUBE 10인 직원에게 이름·전문가 프로필을 주고, 아이소메트릭 사무실에서 직원을
클릭하면 그 직원의 업무보고를 볼 수 있게 하고, 전체·개별 최신화 버튼을 달아라."

구조
    dashboard/template.html   UI 원본(HTML/CSS/JS, `__SNAP_JSON__` 자리표시자)
    scripts/build_dashboard.py  이 파일 — 데이터를 모아 index.html을 굽는다
    index.html                유일한 정본 페이지 (cube/feed/strategy/scenarios/docs는 리다이렉트)

페이지가 읽는 데이터는 두 갈래다.
    1) 빌드 시점 임베드  — `SNAP` 객체(JSON). 파일을 직접 열어도, 위젯 iframe에서도 보인다.
    2) 최신화 버튼       — docs/*.json을 캐시 우회(`?ts=`)로 다시 받아 SNAP을 통째로 교체한다.
       그래서 docs/에 대시보드가 읽는 파일(market_board.json·autotrader_status.json·
       cube_pulse.json)을 같이 발행한다.

실행: python3 scripts/build_dashboard.py [--no-widget]
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from agents import agent_profiles  # noqa: E402
from core import collab_bus  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
DOCS = os.path.join(BASE_DIR, "docs")
TEMPLATE = os.path.join(BASE_DIR, "dashboard", "template.html")
INDEX = os.path.join(BASE_DIR, "index.html")
ASSETS = os.path.join(BASE_DIR, "assets")
DOCS_ASSETS = os.path.join(DOCS, "assets")
WIDGET_BUNDLE = os.path.abspath(os.path.join(BASE_DIR, "..", "widgets", "us-quant-dashboard", "bundle"))

VERSION = "3.0"


def now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ board extract
def build_board(snap: dict) -> dict:
    """전체 market_snapshot(27KB)에서 화면에 그릴 것만 뽑아 공개용 보드로 만든다.

    market_snapshot.json은 런타임 캐시라 git에 올리지 않는다(.gitignore). 대신 이 보드를
    docs/market_board.json으로 발행해 최신화 버튼이 실시간으로 다시 받아갈 수 있게 한다.
    """
    snap = snap or {}
    keep_quote = ("last", "prev_close", "change", "change_pct", "day_high", "day_low",
                  "rvol", "avg_volume_20d", "volume", "last_bar_date")
    quotes = {k: {f: v.get(f) for f in keep_quote if f in v}
              for k, v in (snap.get("quotes") or {}).items() if isinstance(v, dict)}
    macro = {}
    for k, v in (snap.get("macro") or {}).items():
        macro[k] = ({f: v.get(f) for f in keep_quote if f in v} if isinstance(v, dict) else v)
    fut = {k: {f: v.get(f) for f in keep_quote if f in v}
           for k, v in (snap.get("futures") or {}).items() if isinstance(v, dict)}
    return {
        "generated_at_kst": snap.get("generated_at_kst"),
        "phase": snap.get("phase"),
        "data_source": snap.get("data_source"),
        "freshness": snap.get("freshness"),
        "quotes": quotes,
        "macro": macro,
        "futures": fut,
        "bonds": snap.get("bonds") or {},
        "fx_market": snap.get("fx_market") or {},
        "crypto_market": snap.get("crypto_market") or {},
        "cycle": snap.get("cycle") or {},
        "fred": snap.get("fred") or {},
        "clock": snap.get("clock") or {},
    }


# 화면이 실제로 읽는 에이전트 보고 키만 남긴다(전체 보고는 112KB — 임베드 비대화 방지).
REPORT_KEYS = {
    1: ["average_sentiment_score", "overall_market_mood", "data_sources", "sector_sentiment",
        "market_implied_anchor", "key_takeaway"],
    2: ["levels", "actionable_symbols", "best_setup", "cross_asset_summary", "key_takeaway"],
    3: ["earnings_calendar", "macro_calendar", "next_major_event", "key_takeaway"],
    4: ["vix_level", "vix_change_pct", "dxy", "macro_regime", "leading_indicators", "bond_market",
        "risk_off_triggers", "safe_for_leverage", "key_takeaway"],
    5: ["qqq_put_call_oi", "institutional_bias", "credit_flow", "duration_flow", "nvda_gamma_wall",
        "superinvestor_13f", "key_takeaway"],
    6: ["total_scenarios", "atr_basis", "scenarios", "approved_now", "leading_indicator_filter",
        "key_takeaway"],
    7: ["backtest_results", "is_simulation", "friction", "fill_model", "report_generated_at_kst",
        "key_takeaway"],
    8: ["profitable_scenarios", "rejected_scenarios", "recommended_execution_mode", "sgov_vs_cash",
        "crypto_weekend_gap_risk", "cross_market_summary", "key_takeaway"],
    9: ["five_axis", "composite_score", "market_stance", "target_portfolio_weights",
        "lei_leverage_factor", "regime_note", "execution_mode", "approved_strategies", "key_takeaway"],
    10: ["ledger", "bracket_review", "execution_status", "planned_actions", "conservation_controls",
         "key_takeaway"],
}


def trim_report(rep: dict) -> dict:
    rep = dict(rep or {})
    out_agents = []
    for a in rep.get("agents") or []:
        b = {k: v for k, v in a.items() if k != "report"}
        full = a.get("report") or {}
        keys = REPORT_KEYS.get(a.get("agent_id"), ["key_takeaway"])
        b["report"] = {k: full.get(k) for k in keys if k in full}
        out_agents.append(b)
    rep["agents"] = out_agents
    rep.pop("profit_mandate", None)   # 상세는 data/avg_anchor_plan.json 정본
    return rep


# ------------------------------------------------------------------ redirect stubs
def _stub(target: str, label: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} → 통합 대시보드</title>
<link rel="canonical" href="{target}">
<meta http-equiv="refresh" content="0; url={target}">
<script>location.replace("{target}");</script>
<style>
  html,body{{margin:0;height:100%;background:#070b14;color:#cbd5e1;
    font-family:'Pretendard',-apple-system,BlinkMacSystemFont,system-ui,sans-serif}}
  .w{{display:flex;height:100%;align-items:center;justify-content:center;flex-direction:column;gap:10px}}
  a{{color:#38bdf8;text-decoration:none;font-weight:700}}
  small{{color:#64748b}}
</style>
</head>
<body><div class="w">
  <div>이 페이지는 <b>통합 대시보드</b>로 이동했습니다.</div>
  <a href="{target}">통합 대시보드 열기 →</a>
  <small>{label} · 자동 이동이 막히면 위 링크를 누르세요.</small>
</div></body>
</html>
"""


# 리다이렉트로 흡수하는 옛 페이지들 (내용은 index.html 탭으로 통합됨)
STUBS = [
    ("cube.html", "index.html#office", "CUBE 10-Agent 데스크"),
    ("feed.html", "index.html#feed", "10인 업무 피드"),
    ("strategy.html", "index.html#strategy", "전략 상세 가이드"),
    ("scenarios.html", "index.html#scenarios", "시나리오 아카이브"),
]
DOCS_STUBS = [
    ("docs/index.html", "../index.html#overview", "대시보드"),
    ("docs/cube.html", "../index.html#office", "CUBE 10-Agent 데스크"),
    ("docs/feed.html", "../index.html#feed", "10인 업무 피드"),
    ("docs/strategy.html", "../index.html#strategy", "전략 상세 가이드"),
    ("docs/scenarios.html", "../index.html#scenarios", "시나리오 아카이브"),
]


# ------------------------------------------------------------------ assets
def ensure_assets() -> list:
    """사무실 이미지를 모든 경로에 복제한다(pages / assets / docs/assets / 위젯 번들)."""
    made = []
    src_dir = DOCS_ASSETS if os.path.isdir(DOCS_ASSETS) and os.listdir(DOCS_ASSETS) else None
    names = ["office_day.png", "office_night.png", "office_day.webp", "office_night.webp"]
    for target in (ASSETS, DOCS_ASSETS):
        os.makedirs(target, exist_ok=True)
        for n in names:
            src = os.path.join(src_dir, n) if src_dir else None
            dst = os.path.join(target, n)
            if src and os.path.abspath(src) != os.path.abspath(dst) and os.path.exists(src) and not os.path.exists(dst):
                shutil.copyfile(src, dst)
                made.append(dst)
    return made


def mirror_to_widget() -> bool:
    if not os.path.isdir(WIDGET_BUNDLE):
        return False
    shutil.copyfile(INDEX, os.path.join(WIDGET_BUNDLE, "index.html"))
    wa = os.path.join(WIDGET_BUNDLE, "assets")
    os.makedirs(wa, exist_ok=True)
    for n in os.listdir(ASSETS) if os.path.isdir(ASSETS) else []:
        if n.startswith("office_"):
            shutil.copyfile(os.path.join(ASSETS, n), os.path.join(wa, n))
    return True


# ------------------------------------------------------------------ main render
def render(also_widget: bool = True, data_only: bool = False) -> dict:
    """data_only=True면 index.html을 다시 굽지 않고 docs/*.json만 발행한다.

    장중 10분 사이클은 거래 상태 JSON만 갱신하고(대시보드는 '전체 최신화' 버튼이 docs에서
    직접 받아 그린다), index.html은 30분 펄스에서만 다시 굽는다 — 매 10분 370KB HTML을
    커밋하면 저장소가 하루 50MB씩 불어난다.
    """
    feed = load(os.path.join(DATA, "agent_feed.json"), {}) or {}
    report = load(os.path.join(DATA, "cube_report.json"), {}) or {}
    portfolio = load(os.path.join(DATA, "live_portfolio.json"), {}) or {}
    leading = load(os.path.join(DATA, "leading_indicators.json"), {}) or {}
    autotrader = load(os.path.join(DATA, "autotrader_status.json"), {}) or {}
    pulse = load(os.path.join(DATA, "cube_pulse.json"), {}) or {}
    board = build_board(load(os.path.join(DATA, "market_snapshot.json"), {}) or {})
    # 2026-11-19 협업·빌드업 — 버스/장부/클라우드/정리 리포트
    full_bus = load(os.path.join(DATA, "agent_collab_bus.json"), {}) or {}
    threads = collab_bus.build_threads(full_bus.get("messages") or [], limit=14)
    collab = {k: v for k, v in full_bus.items() if k != "messages"}      # 전체 메시지는 docs에서 직접 받는다
    collab["recent_messages"] = list(reversed(full_bus.get("messages") or []))[:40]
    buildup = load(os.path.join(DATA, "strategy_buildup.json"), {}) or {}
    cloud = load(os.path.join(DATA, "cloud_status.json"), {}) or {}
    collab_cycle = load(os.path.join(DATA, "collab_cycle.json"), {}) or {}
    recon = load(os.path.join(DATA, "position_reconciliation.json"), {}) or {}

    if not report.get("team"):
        report["team"] = agent_profiles.as_list()
    if not report.get("office"):
        report["office"] = agent_profiles.office_layout()
    if not board.get("generated_at_kst"):
        board["generated_at_kst"] = (feed.get("last_updated") or "").replace(" KST", "") or None

    snap = {
        "meta": {
            "version": VERSION,
            "built_at_kst": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "repo": "stevepark310/us-capital-preservation",
            "pages": "https://stevepark310.github.io/us-capital-preservation/",
        },
        "team": agent_profiles.as_list(),
        "office": agent_profiles.office_layout(),
        "feed": feed,
        "report": trim_report(report),
        "portfolio": portfolio,
        "leading": leading,
        "autotrader": autotrader,
        "pulse": pulse,
        "board": board,
        "collab": collab,
        "collab_threads": threads,
        "buildup": buildup,
        "cloud": cloud,
        "collab_cycle": collab_cycle,
        "recon": recon,
        # 최신화 버튼이 다시 받아갈 파일 목록(경로는 docs/ 기준)
        "endpoints": {
            "feed": "agent_feed.json",
            "report": "cube_report.json",
            "portfolio": "live_portfolio.json",
            "board": "market_board.json",
            "leading": "leading_indicators.json",
            "autotrader": "autotrader_status.json",
            "pulse": "cube_pulse.json",
            "collab": "agent_collab_bus_full.json",
            "collab_threads": "collab_threads.json",
            "buildup": "strategy_buildup.json",
            "cloud": "cloud_status.json",
            "collab_cycle": "collab_cycle.json",
            "recon": "position_reconciliation.json",
        },
    }

    # 최신화용 공개 파일 발행
    save(os.path.join(DOCS, "market_board.json"), board)
    save(os.path.join(DOCS, "autotrader_status.json"), autotrader)
    save(os.path.join(DOCS, "cube_pulse.json"), pulse)
    # 협업·빌드업: 전체 버스(메시지 포함)는 docs에서 직접 받아가게 발행한다.
    save(os.path.join(DOCS, "agent_collab_bus_full.json"), full_bus)
    save(os.path.join(DOCS, "collab_threads.json"), threads)
    save(os.path.join(DOCS, "strategy_buildup.json"), buildup)
    save(os.path.join(DOCS, "cloud_status.json"), cloud)
    save(os.path.join(DOCS, "collab_cycle.json"), collab_cycle)
    save(os.path.join(DOCS, "position_reconciliation.json"), recon)

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()
    if not data_only:
        payload = json.dumps(snap, ensure_ascii=False, default=str)
        # </script>가 JSON 문자열 안에 들어가도 스크립트가 끊기지 않게 이스케이프
        payload = payload.replace("</", "<\\/")
        html = tpl.replace("__SNAP_JSON__", payload)

        with open(INDEX, "w", encoding="utf-8") as f:
            f.write(html)

        for rel, target, label in STUBS:
            with open(os.path.join(BASE_DIR, rel), "w", encoding="utf-8") as f:
                f.write(_stub(target, label))
        for rel, target, label in DOCS_STUBS:
            path = os.path.join(BASE_DIR, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(_stub(target, label))

        copied = ensure_assets()
        widget = mirror_to_widget() if also_widget else False
    else:
        copied, widget = 0, False

    info = {
        "index_bytes": os.path.getsize(INDEX) if os.path.exists(INDEX) else 0,
        "posts": len(feed.get("posts") or []),
        "built_at_kst": snap["meta"]["built_at_kst"],
        "assets_copied": len(copied) if isinstance(copied, list) else int(copied or 0),
        "widget_mirrored": widget,
        "data_only": bool(data_only),
    }
    if data_only:
        print(f"✅ 대시보드 데이터 발행(docs/*.json) · index.html 재렌더 생략 · {info['built_at_kst']} KST")
        return info
    print(f"✅ 통합 대시보드 index.html {info['index_bytes'] / 1024:.0f}KB · "
          f"업무보고 {info['posts']}건 · 직원 {len(agent_profiles.EMPLOYEES)}명 · "
          f"{info['built_at_kst']} KST" + (f" · 위젯 번들 동기화" if widget else ""))
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-widget", action="store_true")
    ap.add_argument("--data-only", action="store_true")
    a = ap.parse_args()
    render(also_widget=not a.no_widget, data_only=a.data_only)

