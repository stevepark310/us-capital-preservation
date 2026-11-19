"""
Agent Collab Bus — CUBE 10인이 서로 업무 결과를 주고받는 사내 메시지 버스.

동훈님 지시(2026-11-19): "모든 직원이 상시로 계속 일하면서 서로 업무 결과를 주고 받고
지속 빌드업 하면서 투자 수익을 지속 향상시킬 수 있도록."

지금까지 10인 피드는 각자 자기 상태를 한 줄씩 올리는 **방송**이었다. 여기서는
한 사람의 결과가 다음 사람의 입력이 되는 **핸드오프**를 남긴다.

    리서치 데스크(1~5)  업무보고 + 인사이트 + 가설 발행
        ↓  correlation_id로 묶인 핸드오프
    검증 데스크(6~8)    측정 요청 → 실측(In/Out-of-Sample) → 승인/보류/개선 피드백
        ↓
    총괄·집행 데스크(9~10) 승인 반영 / 자동 적용 / 체결 복기 피드백

파일: `data/agent_collab_bus.json` (최근 N건만 유지, 스레드는 읽을 때 조립)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUS_PATH = os.path.join(BASE_DIR, "data", "agent_collab_bus.json")

MAX_MESSAGES = 400

DESK_RESEARCH = [1, 2, 3, 4, 5]
DESK_VALIDATION = [6, 7, 8]
DESK_EXECUTIVE = [9, 10]
DESKS: Dict[int, str] = {
    **{i: "RESEARCH" for i in DESK_RESEARCH},
    **{i: "VALIDATION" for i in DESK_VALIDATION},
    **{i: "EXECUTIVE" for i in DESK_EXECUTIVE},
}
DESK_KO = {"RESEARCH": "리서치·시그널", "VALIDATION": "검증·리스크", "EXECUTIVE": "총괄·집행"}

KINDS = [
    "DUTY_REPORT",        # 담당 업무 현황 (10분 보고)
    "INSIGHT",            # 실측 지표에서 뽑은 관측
    "HYPOTHESIS",         # 검증 가능한 가설 제안
    "VALIDATION_REQUEST", # 검증 데스크에 측정 요청
    "VALIDATION_RESULT",  # 실측 결과(IS/OOS·PF·MDD)
    "APPROVAL",           # 게이트 통과 → 적용 승인
    "HOLD",               # 게이트 미통과 → 보류/폐기 + 사유
    "FEEDBACK",           # 개선 요청(다음 사이클 입력)
    "EXECUTION_REVIEW",   # 체결·브래킷 복기
    "CLOUD_STATUS",       # 클라우드 상시 가동 상태
]
KIND_KO = {
    "DUTY_REPORT": "업무보고", "INSIGHT": "인사이트", "HYPOTHESIS": "가설",
    "VALIDATION_REQUEST": "검증 요청", "VALIDATION_RESULT": "검증 결과",
    "APPROVAL": "승인", "HOLD": "보류", "FEEDBACK": "피드백",
    "EXECUTION_REVIEW": "체결 복기", "CLOUD_STATUS": "클라우드 상태",
}


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def load() -> Dict[str, Any]:
    try:
        with open(BUS_PATH, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    doc.setdefault("version", "1.0")
    doc.setdefault("messages", [])
    doc.setdefault("cycle", 0)
    return doc


def save(doc: Dict[str, Any]) -> Dict[str, Any]:
    msgs = (doc.get("messages") or [])[-MAX_MESSAGES:]
    doc["messages"] = msgs
    doc["updated_at_kst"] = _now_kst()
    doc["stats"] = summary(doc)
    os.makedirs(os.path.dirname(BUS_PATH), exist_ok=True)
    with open(BUS_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return doc


def _next_seq(doc: Dict[str, Any]) -> int:
    return len(doc.get("messages") or []) + 1


def post(doc: Dict[str, Any], sender: int, sender_name: str, kind: str,
         subject: str, body: str, to: Optional[List[int]] = None,
         metrics: Optional[Dict[str, Any]] = None, correlation_id: Optional[str] = None,
         refs: Optional[Dict[str, Any]] = None, phase: Optional[str] = None) -> Dict[str, Any]:
    """메시지 1건 발행. `to`는 수신자 agent_id 목록(브로드캐스트면 비움)."""
    kind = kind.upper()
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind}")
    ts = _now_kst()
    cycle = int(doc.get("cycle") or 0)
    msg = {
        "id": f"m{cycle:05d}-{_next_seq(doc):05d}",
        "ts_kst": ts,
        "cycle": cycle,
        "from": int(sender), "from_name": sender_name, "from_desk": DESKS.get(int(sender)),
        "to": list(to or []), "kind": kind, "kind_ko": KIND_KO[kind],
        "subject": subject, "body": body,
        "metrics": metrics or {},
        "correlation_id": correlation_id,
        "refs": refs or {},
        "phase": phase,
    }
    doc.setdefault("messages", []).append(msg)
    return msg


def thread_id(doc: Dict[str, Any], tag: str) -> str:
    return f"T{int(doc.get('cycle') or 0):05d}-{tag}"


def messages_of(doc: Dict[str, Any], agent_id: Optional[int] = None,
                kind: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    out = []
    for m in reversed(doc.get("messages") or []):
        if agent_id is not None and m.get("from") != agent_id and agent_id not in (m.get("to") or []):
            continue
        if kind and m.get("kind") != kind.upper():
            continue
        out.append(m)
        if len(out) >= limit:
            break
    return out


def summary(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = doc or load()
    msgs = doc.get("messages") or []
    by_kind: Dict[str, int] = {}
    by_desk: Dict[str, int] = {}
    last: Dict[int, Dict[str, Any]] = {}
    for m in msgs:
        by_kind[m.get("kind")] = by_kind.get(m.get("kind"), 0) + 1
        by_desk[m.get("from_desk")] = by_desk.get(m.get("from_desk"), 0) + 1
        last[int(m.get("from") or 0)] = {"ts_kst": m.get("ts_kst"), "kind": m.get("kind"),
                                         "subject": m.get("subject")}
    threads = build_threads(msgs)
    return {
        "total": len(msgs),
        "by_kind": by_kind, "by_kind_ko": {KIND_KO.get(k, k): v for k, v in by_kind.items()},
        "by_desk": by_desk, "by_desk_ko": {DESK_KO.get(k, k): v for k, v in by_desk.items()},
        "threads": len(threads),
        "handoffs": sum(1 for m in msgs if m.get("to")),
        "last_per_agent": {str(k): v for k, v in sorted(last.items())},
        "last_message_at_kst": msgs[-1]["ts_kst"] if msgs else None,
        "cycle": int(doc.get("cycle") or 0),
    }


def build_threads(msgs: Optional[List[Dict[str, Any]]] = None, limit: int = 12) -> List[Dict[str, Any]]:
    """correlation_id로 묶어 핸드오프 흐름을 만든다(최신 스레드부터)."""
    msgs = msgs if msgs is not None else (load().get("messages") or [])
    by_id: Dict[str, Dict[str, Any]] = {}
    for m in msgs:
        cid = m.get("correlation_id")
        if not cid:
            continue
        t = by_id.setdefault(cid, {"correlation_id": cid, "steps": [], "subject": m.get("subject")})
        t["steps"].append(m)
        t["subject"] = t["subject"] or m.get("subject")
    out = []
    for t in by_id.values():
        steps = t["steps"]
        out.append({
            "correlation_id": t["correlation_id"],
            "subject": t["subject"],
            "started_at_kst": steps[0].get("ts_kst"),
            "status": _thread_status(steps),
            "path": [f"{s.get('from_name')}→{s.get('kind_ko')}" for s in steps],
            "steps": [{k: s.get(k) for k in ("id", "ts_kst", "from", "from_name", "to", "kind", "kind_ko",
                                             "subject", "body", "metrics")} for s in steps],
        })
    out.sort(key=lambda x: x.get("started_at_kst") or "", reverse=True)
    return out[:limit]


def _thread_status(steps: List[Dict[str, Any]]) -> str:
    kinds = [s.get("kind") for s in steps]
    if "APPROVAL" in kinds:
        return "APPLIED" if "EXECUTION_REVIEW" in kinds else "APPROVED"
    if "HOLD" in kinds:
        return "HELD"
    if "VALIDATION_RESULT" in kinds:
        return "MEASURED"
    if "VALIDATION_REQUEST" in kinds:
        return "IN_REVIEW"
    return "OPEN"
