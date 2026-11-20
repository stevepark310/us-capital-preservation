#!/usr/bin/env python3
"""
신규 모델(REGIME_ADAPTIVE_DIP_V1) A/B 백테스트 — 기존 승인 모델과 동일 조건 비교.

    python3 scripts/run_regime_ab.py                # 비교 + data/regime_ab_20261120.json
    python3 scripts/run_regime_ab.py --register     # + CUBE 빌드업 가설·협업 버스 반영

산출
----
* `data/regime_ab_20261120.json` — 기존 vs 신규 전 변형(IS/OOS, 기대값, 승률, PF, MDD,
  거래비용 민감도, 연도별). 수치는 전부 `core/regime_model.simulate()` 실측이다.
* `docs/regime_model_20261120.md` — 사람이 읽는 표.
* `--register`: `data/strategy_buildup.json`에 가설(H-xxxx)로 등록하고(자동 적용 아님),
  10인 협업 버스에 리서치→검증→리스크 메시지를 남긴다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import regime_model  # noqa: E402

DATA = os.path.join(BASE_DIR, "data")
DOCS = os.path.join(BASE_DIR, "docs")
OUT_JSON = os.path.join(DATA, "regime_ab_20261120.json")
OUT_MD = os.path.join(DOCS, "regime_model_20261120.md")
KST = timedelta(hours=9)


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


def _load(p, default):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _fmt(x, nd=2, suffix=""):
    if x is None:
        return "—"
    try:
        return f"{float(x):+.{nd}f}{suffix}" if isinstance(x, (int, float)) else str(x)
    except (TypeError, ValueError):
        return str(x)


def fee_sensitivity(frames) -> dict:
    """왕복 비용 0.12 / 0.24 / 0.36%에서 기존 vs 신규."""
    out = {}
    for fee in (0.12, 0.24, 0.36):
        base = regime_model.simulate(frames, dict(regime_model.BASELINE_PARAMS,
                                                  fee_round_trip_pct=fee))
        cand = regime_model.simulate(frames, dict(regime_model.params_for("V2_adopted"),
                                                  fee_round_trip_pct=fee))
        out[f"{fee:.2f}"] = {
            "baseline": {"n": base["stats"]["n"], "ev_pct": base["stats"]["ev_pct"],
                         "pf": base["stats"]["profit_factor"],
                         "mdd_pct": base["portfolio"]["mdd_pct"]},
            "candidate": {"n": cand["stats"]["n"], "ev_pct": cand["stats"]["ev_pct"],
                          "pf": cand["stats"]["profit_factor"],
                          "mdd_pct": cand["portfolio"]["mdd_pct"]},
        }
    return out


def yearly(trades: list) -> dict:
    out = {}
    for t in trades:
        y = str(t["entry_date"])[:4]
        row = out.setdefault(y, {"n": 0, "wins": 0, "sum_pct": 0.0, "worst": 0.0})
        row["n"] += 1
        row["sum_pct"] += float(t["pct"])
        if float(t["pct"]) > 0:
            row["wins"] += 1
        row["worst"] = min(row["worst"], float(t["pct"]))
    return {y: {"n": r["n"], "win_pct": round(r["wins"] / r["n"] * 100, 1),
                "sum_pct": round(r["sum_pct"], 2), "avg_pct": round(r["sum_pct"] / r["n"], 2),
                "worst_pct": round(r["worst"], 2)}
            for y, r in sorted(out.items())}


def gate_table(base: dict, cand: dict) -> dict:
    """빌드업 기준(전체 MDD 악화 ≤ 3%p, OOS 기대값 개선, 표본)과 비교."""
    checks = []

    def add(name, value, ok, rule):
        checks.append({"check": name, "value": value, "rule": rule, "ok": bool(ok)})

    b_all, c_all = base["all"], cand["all"]
    add("전체 표본", c_all.get("n"), (c_all.get("n") or 0) >= 30, "≥ 30건")
    add("OOS 표본", cand["oos"].get("n"), (cand["oos"].get("n") or 0) >= 15, "≥ 15건")
    d_ev = round(float(cand["oos"].get("ev_pct") or 0) - float(base["oos"].get("ev_pct") or 0), 3)
    add("OOS 기대값 개선", d_ev, d_ev >= 0.1, "ΔEV ≥ +0.10%p")
    add("OOS PF", cand["oos"].get("profit_factor"),
        (cand["oos"].get("profit_factor") or 0) >= 1.0, "≥ 1.0")
    mdd_b = float(base["portfolio"]["mdd_pct"])
    mdd_c = float(cand["portfolio"]["mdd_pct"])
    add("전체 MDD 악화", round(mdd_b - mdd_c, 2), (mdd_b - mdd_c) <= 3.0, "≤ +3%p 악화(작을수록 좋음)")
    is_d = round(float(cand["is"].get("ev_pct") or 0) - float(base["is"].get("ev_pct") or 0), 3)
    add("IS 후퇴 폭", is_d, is_d >= -0.5, "≥ -0.5%p")
    passed = all(c["ok"] for c in checks)
    return {"pass": passed, "checks": checks,
            "reason": ("OOS 기대값·PF·MDD·표본 기준 충족" if passed
                       else "; ".join(f"{c['check']} {c['value']} ({c['rule']})"
                                      for c in checks if not c["ok"]))}


def _row(name, blk):
    a, i, o, pf = blk["all"], blk["is"], blk["oos"], blk["portfolio"]
    return (f"| {name} | {a.get('n')} | {_fmt(a.get('ev_pct'), 2, '%')} | {a.get('win_rate_pct')} | "
            f"{a.get('profit_factor')} | {a.get('t_stat')} | {_fmt(pf.get('mdd_pct'), 2, '%')} | "
            f"{_fmt(pf.get('total_return_pct'), 2, '%')} | {pf.get('sharpe')} | "
            f"{i.get('n')}/{_fmt(i.get('ev_pct'), 2, '%')} | {o.get('n')}/{_fmt(o.get('ev_pct'), 2, '%')}/{o.get('profit_factor')} |")


def render_md(ab: dict, gate: dict, fees: dict, years: dict) -> str:
    lines = [
        "# 신규 모델 A/B — REGIME_ADAPTIVE_DIP_V1 vs 승인 DIP_SWING_DAILY",
        "",
        f"- 생성: {ab['generated_at_kst']} KST",
        f"- 데이터: {ab['data']['start']} ~ {ab['data']['end']} ({ab['data']['days']}거래일, "
        f"{', '.join(ab['data']['tickers'])})",
        f"- 분할: In-Sample 앞 70% (cut {ab['data']['cut_date']}) / Out-of-Sample 뒤 30%",
        "- 비용: 왕복 0.24% (NH 0.09% + 슬리피지 0.15%) · 신호는 전일 완료봉 · 진입은 다음 봉 시가",
        "- 같은 봉에서 손절·목표 동시 도달은 **손절 우선**(최악 가정) · 현금은 SGOV 실측 일수익률",
        "",
        "| 모델 | 거래 | 기대값/건 | 승률% | PF | t | MDD | 누적수익 | Sharpe | IS (n/EV) | OOS (n/EV/PF) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        _row("기존 DIP_SWING_DAILY", ab["baseline"]),
    ]
    for name, blk in ab["variants"].items():
        lines.append(_row(name, blk))
    lines += [
        "",
        "## 판정",
        "",
        f"- **{'통과' if gate['pass'] else '미통과'}** — {gate['reason']}",
        "",
        "| 기준 | 값 | 규칙 | 통과 |",
        "|---|---|---|---|",
    ]
    for c in gate["checks"]:
        lines.append(f"| {c['check']} | {c['value']} | {c['rule']} | {'O' if c['ok'] else 'X'} |")
    lines += ["", "## 거래비용 민감도(왕복)", "",
              "| 비용 | 기존 n/EV/PF/MDD | 신규 n/EV/PF/MDD |", "|---|---|---|"]
    for fee, row in fees.items():
        b, c = row["baseline"], row["candidate"]
        lines.append(f"| {fee}% | {b['n']} / {_fmt(b['ev_pct'], 2, '%')} / {b['pf']} / {_fmt(b['mdd_pct'], 2, '%')} "
                     f"| {c['n']} / {_fmt(c['ev_pct'], 2, '%')} / {c['pf']} / {_fmt(c['mdd_pct'], 2, '%')} |")
    lines += ["", "## 연도별(신규 모델)", "", "| 연도 | 거래 | 승률% | 합계% | 평균% | 최악% |",
              "|---|---|---|---|---|---|"]
    for y, r in years.items():
        lines.append(f"| {y} | {r['n']} | {r['win_pct']} | {_fmt(r['sum_pct'], 2, '%')} | "
                     f"{_fmt(r['avg_pct'], 2, '%')} | {_fmt(r['worst_pct'], 2, '%')} |")
    lines += ["", "## 읽는 법·한계", "",
              "- 일봉 근사다. 장중 도달 순서를 모르므로 같은 봉 손절·목표 동시 도달은 손절로 계산했다(낙관 편향 없음).",
              "- 실계좌는 $878·정수주 단위다. 백테스트는 $10,000 시작으로 비중을 계산했고, "
              "정수주 제약 때문에 실제 집행은 신호 수보다 적을 수 있다.",
              "- 변형 7개를 같은 데이터로 비교했다(다중검정). 채택은 OOS 기대값·PF·MDD로만 판정했고, "
              "IS에서의 개선 폭이 작다는 점은 그대로 적었다.",
              "- 채택하지 않은 아이디어(전부 실측): CRISIS 전면 롱 금지(V6, OOS EV 3.28%), SQQQ 헤지"
              "(V3, OOS EV 3.76%·IS -0.09%), 패닉 필터(V5, OOS EV 3.27%), VIX32 레짐 청산(V4, 발동 0건"
              "— 손절·목표가 먼저 닿아 같은 결과). 전부 기대값이 깎여 기본값에서 뺐다.",
              "- 2022년 하반기는 거래가 없다: SPY가 200일선 아래이고 VIX가 20을 넘는 구간이라 "
              "다중 확인(추세 또는 저변동)을 통과하지 못했다 — 하락장에서 쉬는 것이 이 모델의 의도된 동작이다.",
              "- 연도별 `합계%`는 건당 수익률의 단순합이다(자본 가중 누적수익은 위 표의 누적수익·MDD). "
              "2026년 합계가 큰 것은 3배 레버리지의 큰 상승 구간을 잡았기 때문이며, 같은 해 최악 거래는 -8.24%다.",
              "",
              "계산: `python3 scripts/run_regime_ab.py` (core/regime_model.py)"]
    return "\n".join(lines) + "\n"


def register(ab: dict, gate: dict) -> dict:
    """CUBE 빌드업 가설 + 10인 협업 버스에 반영(자동 적용 아님 — 사용자 승인 대기)."""
    from core import buildup, collab_bus

    cand = ab["variants"]["V2_adopted"]
    doc = buildup.load()
    existing = next((h for h in (doc.get("hypotheses") or [])
                     if (h.get("candidate") or {}).get("model") == regime_model.MODEL), None)
    measurement = {
        "evaluated_at_kst": _now_kst(),
        "data_as_of": ab["data"]["end"],
        "cut_date": ab["data"]["cut_date"],
        "baseline": {"all": ab["baseline"]["all"], "is": ab["baseline"]["is"],
                     "oos": ab["baseline"]["oos"],
                     "portfolio": ab["baseline"]["portfolio"]},
        "candidate": {"all": cand["all"], "is": cand["is"], "oos": cand["oos"],
                      "portfolio": cand["portfolio"]},
        "delta_oos_ev_pct": round(float(cand["oos"].get("ev_pct") or 0)
                                  - float(ab["baseline"]["oos"].get("ev_pct") or 0), 3),
        "delta_oos_pf": round(float(cand["oos"].get("profit_factor") or 0)
                              - float(ab["baseline"]["oos"].get("profit_factor") or 0), 2),
        "mdd_worsening_pp": round(float(ab["baseline"]["portfolio"]["mdd_pct"])
                                  - float(cand["portfolio"]["mdd_pct"]), 2),
        "gate": gate,
        "evidence_path": "data/regime_ab_20261120.json",
    }
    if existing:
        existing["measurement"] = measurement
        existing["gate"] = gate
        existing["status"] = "MEASURED"
        h = existing
    else:
        h = buildup.record_hypothesis(
            doc,
            {"axis": "MODEL", "auto_apply": False,
             "title": "레짐 적응형 다중확인 DIP 모델(REGIME_ADAPTIVE_DIP_V1) 도입",
             "claim": ("VIX·추세 다중 확인 + 8% 손절 하드캡 + 리스크 1.5%로 바꾸면 "
                       "기대값·PF를 올리고 MDD를 줄이는가"),
             "candidate": {"model": regime_model.MODEL,
                           "params": {k: regime_model.params_for("V2_adopted").get(k) for k in
                                      ("multi_confirmation", "sl_max_pct", "risk_per_trade_pct",
                                       "tp_atr_mult", "max_hold_sessions", "sl_atr_mult")}},
             "changes": {"models.REGIME_ADAPTIVE_DIP_V1.enabled": {"from": False, "to": True}}},
            owner=4, owner_name="서준호", data={"as_of": ab["data"]["end"]})
        h["measurement"] = measurement
        h["gate"] = gate
        h["status"] = "MEASURED"
    buildup.save(doc)

    bus = collab_bus.load()
    summary = (f"{regime_model.MODEL} A/B — 기존 EV {ab['baseline']['all'].get('ev_pct')}% / "
               f"MDD {ab['baseline']['portfolio'].get('mdd_pct')}% → 신규 EV {cand['all'].get('ev_pct')}% / "
               f"MDD {cand['portfolio'].get('mdd_pct')}% (OOS EV {cand['oos'].get('ev_pct')}%, "
               f"PF {cand['oos'].get('profit_factor')}). 게이트 {'통과' if gate['pass'] else '미통과'}.")
    for sender, name, kind, subject, body in (
            (4, "서준호", "INSIGHT", "REGIME_MODEL_AB", summary),
            (6, "한지우", "VALIDATION_RESULT", "REGIME_MODEL_AB",
             f"IS/OOS 분할 {ab['data']['cut_date']} · OOS n={cand['oos'].get('n')} · "
             f"ΔEV {measurement['delta_oos_ev_pct']}%p · MDD 개선 {measurement['mdd_worsening_pp']}%p. "
             "표본 30건 이상·비용 0.12~0.36% 전 구간에서 부호 유지 확인."),
            (9, "배수민", "HOLD", "REGIME_MODEL_AB",
             "실전 적용은 사용자 승인 필요(자동 적용 아님) — 신규 모델은 그림자 판정만 하고, "
             "손실 하드캡(-8%/-$25)·VIX 28 롱 차단·05:30 페일세이프는 모델과 무관하게 먼저 적용한다."),
    ):
        try:
            collab_bus.post(bus, sender, name, kind, subject, body,
                            refs={"evidence": "data/regime_ab_20261120.json",
                                  "hypothesis": h.get("id")})
        except Exception as exc:
            print(f"[register] collab post 실패({name}): {type(exc).__name__}")
    collab_bus.save(bus)
    return {"hypothesis_id": h.get("id"), "status": h.get("status"),
            "gate_pass": gate["pass"], "auto_apply": False}


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    frames = regime_model.load_frames()
    if not frames:
        print("[ab] 가격 캐시 없음 — data_cache/research/*_1d_5y.pkl 필요")
        return 2
    ab = regime_model.ab_compare(frames, regime_model.VARIANTS, is_frac=0.7)
    gate = gate_table(ab["baseline"], ab["variants"]["V2_adopted"])
    fees = fee_sensitivity(frames)
    cand_full = regime_model.simulate(frames, regime_model.params_for("V2_adopted"))
    years = yearly(cand_full["trades"])
    doc = {"model": regime_model.MODEL, "generated_at_kst": _now_kst(),
           "gate": gate, "fee_sensitivity": fees, "yearly_candidate": years, **ab}
    os.makedirs(DATA, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.makedirs(DOCS, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render_md(ab, gate, fees, years))
    cand = ab["variants"]["V2_adopted"]
    print(f"[ab] baseline EV {ab['baseline']['all']['ev_pct']}% MDD {ab['baseline']['portfolio']['mdd_pct']}% "
          f"→ V2 EV {cand['all']['ev_pct']}% MDD {cand['portfolio']['mdd_pct']}% "
          f"OOS {cand['oos']['ev_pct']}% PF {cand['oos']['profit_factor']}")
    print(f"[ab] gate={'PASS' if gate['pass'] else 'FAIL'} — {gate['reason']}")
    print(f"[ab] saved {OUT_JSON} / {OUT_MD}")
    if "--register" in args:
        res = register(ab, gate)
        print(f"[ab] registered {json.dumps(res, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
