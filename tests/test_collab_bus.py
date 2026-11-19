"""협업 버스 — 발신/수신·스레드 조립·요약·보관 상한."""
import json

import pytest

from core import collab_bus as bus


@pytest.fixture()
def doc(tmp_path, monkeypatch):
    monkeypatch.setattr(bus, "BUS_PATH", str(tmp_path / "agent_collab_bus.json"))
    d = bus.load()
    return d


def test_post_records_route_and_thread(doc):
    cid = bus.thread_id(doc, "HYP1")
    bus.post(doc, 2, "이도현", "HYPOTHESIS", "가설 A", "본문", to=[6, 7, 8],
             metrics={"ATR%": 23.8}, correlation_id=cid)
    bus.post(doc, 6, "강우진", "VALIDATION_REQUEST", "측정 설계", "설계 본문", to=[7], correlation_id=cid)
    bus.post(doc, 7, "윤태호", "VALIDATION_RESULT", "실측 결과", "결과 본문", to=[8, 9], correlation_id=cid)
    bus.post(doc, 8, "임수아", "APPROVAL", "승인", "통과", to=[9, 10], correlation_id=cid)
    bus.save(doc)

    assert len(doc["messages"]) == 4
    assert doc["messages"][0]["from_desk"] == "RESEARCH"
    assert doc["messages"][3]["from_desk"] == "VALIDATION"
    threads = bus.build_threads(doc["messages"])
    assert threads[0]["correlation_id"] == cid
    assert threads[0]["status"] == "APPROVED"
    assert len(threads[0]["steps"]) == 4
    assert "이도현→가설" in threads[0]["path"][0]


def test_hold_closes_thread(doc):
    cid = bus.thread_id(doc, "HYP2")
    bus.post(doc, 2, "이도현", "HYPOTHESIS", "가설 B", "본문", to=[6], correlation_id=cid)
    bus.post(doc, 8, "임수아", "HOLD", "보류", "게이트 미통과", to=[9], correlation_id=cid)
    assert bus.build_threads(doc["messages"])[0]["status"] == "HELD"


def test_summary_counts_by_desk_and_kind(doc):
    bus.post(doc, 1, "한지우", "INSIGHT", "s1", "b1", to=[6])
    bus.post(doc, 7, "윤태호", "VALIDATION_RESULT", "s2", "b2", to=[8])
    s = bus.summary(doc)
    assert s["total"] == 2
    assert s["by_desk_ko"]["리서치·시그널"] == 1
    assert s["by_kind_ko"]["검증 결과"] == 1
    assert s["handoffs"] == 2


def test_unknown_kind_is_rejected(doc):
    with pytest.raises(ValueError):
        bus.post(doc, 1, "한지우", "GOSSIP", "s", "b")


def test_messages_are_capped_on_save(doc, monkeypatch):
    monkeypatch.setattr(bus, "MAX_MESSAGES", 5)
    for i in range(9):
        bus.post(doc, 1, "한지우", "DUTY_REPORT", f"s{i}", "b")
    bus.save(doc)
    saved = json.loads(open(bus.BUS_PATH, encoding="utf-8").read())
    assert len(saved["messages"]) == 5
    assert saved["messages"][-1]["subject"] == "s8"


def test_round_trip_persists_cycle(doc):
    doc["cycle"] = 7
    bus.save(doc)
    again = bus.load()
    assert again["cycle"] == 7
    assert again["stats"]["total"] == 0
