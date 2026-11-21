#!/usr/bin/env python3
"""
CUBE-M8 자동 수집·갱신 (지속 파이프라인 실행기)
================================================

동훈님 지시(2026-11-21 11:01): "지속적으로 모든 정보를 자동으로 수집하고
알고리즘에 반영할 수 있도록".

무엇을 하나
    - FRED(금리·기대인플레·고용·CPI·PPI) + Yahoo(비트코인·금·유가·주가) 원계열 수집
    - 8블록 z → 복합지수(CUBE-M8) 계산 → data/macro_leading.json · docs/macro_leading.json
    - CUBE-LEI(leading_indicators.json)에 macro8 블록을 붙여 대시보드·에이전트가 즉시 읽게 함
    - `--backtest`를 주면 예측력 A/B 백테스트까지 실행(주 1회 권장)

사용
    python3 scripts/sync_macro_leading.py                # 수집·계산·발행
    python3 scripts/sync_macro_leading.py --backtest      # + 백테스트
    python3 scripts/sync_macro_leading.py --if-stale 3    # 발행본이 3시간보다 오래됐을 때만
    python3 scripts/sync_macro_leading.py --force         # 캐시 무시하고 원계열 재수집

주문은 절대 하지 않는다(읽기·계산·기록 전용).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import macro_leading as m8          # noqa: E402


def _attach_to_lei(res: dict) -> dict:
    """leading_indicators.json에도 macro8을 붙여 대시보드·에이전트가 같은 값을 쓰게 한다."""
    from core import leading_indicators
    path = leading_indicators.LEI_OUT
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return {"attached": False, "reason": "leading_indicators.json 없음(다음 오케스트레이터 실행에서 생성)"}
    doc["macro8"] = m8._compact(res)
    payload = json.dumps(doc, ensure_ascii=False, indent=2, default=str)
    for p in (leading_indicators.LEI_OUT, leading_indicators.DOCS_LEI_OUT):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)
    return {"attached": True, "lei_updated_at_kst": doc.get("generated_at_kst")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CUBE-M8 선행지표 자동 수집기")
    ap.add_argument("--backtest", action="store_true", help="예측력 백테스트도 실행")
    ap.add_argument("--if-stale", type=float, default=None, metavar="HOURS",
                    help="발행본이 이 시간보다 오래됐을 때만 수집")
    ap.add_argument("--force", action="store_true", help="원계열 캐시 무시")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.if_stale is not None:
        age = m8._latest_json_age_h()
        if age <= float(args.if_stale):
            print(f"[m8] 최신({age:.2f}h) — 수집 생략 (임계 {args.if_stale}h)")
            return 0

    res = m8.export(m8.compute(force=args.force, quiet=args.quiet))
    attach = _attach_to_lei(res)
    comp = res.get("composite") or {}
    print(f"[m8] z {comp.get('z')} · {comp.get('regime_ko')} · 지수 {comp.get('index')} · "
          f"성분 {comp.get('components_available')}/{comp.get('components_total')} · "
          f"노출계수 {comp.get('linear_exposure_scale')} (mode={(res.get('policy') or {}).get('mode')}) · "
          f"LEI 첨부 {'OK' if attach.get('attached') else attach.get('reason')}")

    if args.backtest:
        from scripts import run_macro_lei_backtest as bt    # noqa: WPS433 (스크립트 내부 import)
        out = bt.run_backtest(force=args.force, quiet=args.quiet)
        print(f"[m8] 백테스트 판정: {(out.get('verdict') or {}).get('mode')} — "
              f"{(out.get('verdict') or {}).get('summary_ko')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
