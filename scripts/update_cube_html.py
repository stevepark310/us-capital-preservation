#!/usr/bin/env python3
"""(호환용) 과거 cube.html 패치 스크립트 → 통합 대시보드 렌더로 위임."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.build_dashboard import render
render()
