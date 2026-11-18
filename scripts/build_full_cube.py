#!/usr/bin/env python3
"""
(호환용 진입점) 대시보드 렌더.

2026-11-18 통합 개편 전에는 이 파일이 cube.html을 문자열 치환으로 패치하고 feed.html·
docs/ 복사본을 따로 만들었다. 지금은 페이지가 `index.html` 한 장이고, 렌더러는
`scripts/build_dashboard.py`(+ `dashboard/template.html`)다.

기존 호출자(scripts/run_10m_pulse.py, 예약 작업, 워크플로)를 깨지 않기 위해 이름만 남긴다.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.build_dashboard import render  # noqa: E402


if __name__ == "__main__":
    render(also_widget="--no-widget" not in sys.argv[1:])
    print("✅ cube.html·feed.html·docs/*.html 은 통합 대시보드(index.html)로 리다이렉트됩니다.")
