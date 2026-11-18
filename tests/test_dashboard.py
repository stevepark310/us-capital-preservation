"""통합 대시보드·직원 프로필 회귀 테스트 (2026-11-18 개편).

지키려는 것
  · 페이지는 index.html 한 장이고, 옛 페이지들은 그리로 리다이렉트된다(중복 제거).
  · 직원 10명의 이름·직함·좌석이 프로필 정본 한 곳에서 나온다.
  · 사무실 이미지가 assets/·docs/assets/ 양쪽에 있어 어떤 경로에서도 깨지지 않는다.
"""
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from agents import agent_profiles  # noqa: E402
from scripts.build_dashboard import render  # noqa: E402


def _read(rel):
    with open(os.path.join(BASE_DIR, rel), "r", encoding="utf-8") as f:
        return f.read()


def test_profiles_are_complete_and_unique():
    emps = agent_profiles.EMPLOYEES
    assert [e["id"] for e in emps] == list(range(1, 11))
    assert len({e["name"] for e in emps}) == 10
    assert len({(e["seat"]["x"], e["seat"]["y"]) for e in emps}) == 10
    for e in emps:
        assert e["name"].isalnum() or len(e["name"]) >= 2
        assert e["title"] and e["specialty"] and e["duty"] and e["metrics"]
        assert 0 < e["seat"]["x"] < 100 and 0 < e["seat"]["y"] < 100
    # 좌석은 2열 x 5석 구조(안쪽 5 + 앞줄 5)
    assert sum(1 for e in emps if e["seat"]["row"] == "BACK") == 5
    assert sum(1 for e in emps if e["seat"]["row"] == "FRONT") == 5


def test_pages_are_redirected_to_single_dashboard():
    for rel, target in [("cube.html", "index.html#office"), ("feed.html", "index.html#feed"),
                        ("strategy.html", "index.html#strategy"), ("scenarios.html", "index.html#scenarios"),
                        ("docs/index.html", "../index.html#overview")]:
        html = _read(rel)
        assert target in html, rel
        assert 'http-equiv="refresh"' in html or "location.replace" in html


def test_dashboard_renders_team_office_and_data():
    info = render(also_widget=False)
    html = _read("index.html")
    assert "__SNAP_JSON__" not in html
    assert info["posts"] >= 10
    for e in agent_profiles.EMPLOYEES:
        assert e["name"] in html, e["name"]
        assert str(e["seat"]["x"]) in html and str(e["seat"]["y"]) in html, e["name"]
    for marker in ("id=\"p-overview\"", "id=\"p-office\"", "id=\"p-macro\"", "id=\"p-cross\"",
                   "id=\"p-scenarios\"", "id=\"p-strategy\"", "id=\"p-feed\"",
                   "function renderOffice", "function refreshAll", "function refreshEmployee",
                   "office_day", "office_night"):
        assert marker in html, marker
    # 임베드된 SNAP이 유효한 JSON인지
    m = re.search(r"const SNAP = (\{.*?\});\nconst TEAM", html, re.S)
    assert m
    snap = json.loads(m.group(1).replace("<\\/", "</"))
    assert len(snap["team"]) == 10 and len(snap["office"]["seats"]) == 10
    assert "feed" in snap and "report" in snap


def test_office_assets_available_on_every_path():
    for rel in ("docs/assets/office_day.png", "docs/assets/office_night.png",
                "assets/office_day.png", "assets/office_night.png"):
        path = os.path.join(BASE_DIR, rel)
        assert os.path.exists(path), rel
        assert os.path.getsize(path) > 100_000, rel
    # 템플릿이 가리키는 상대 경로
    html = _read("index.html")
    assert 'src="assets/office_day.png"' in html and 'src="assets/office_night.png"' in html


def test_feed_posts_carry_employee_names():
    path = os.path.join(BASE_DIR, "data", "agent_feed.json")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        feed = json.load(f)
    names = {e["id"]: e["name"] for e in agent_profiles.EMPLOYEES}
    for p in feed.get("posts") or []:
        aid = p.get("agent_id")
        if aid in names and p.get("agent_role") != "AUTONOMOUS_INTRADER":
            assert p.get("agent_person") in (None, names[aid]), p.get("id")
