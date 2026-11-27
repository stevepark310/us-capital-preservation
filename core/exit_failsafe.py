"""
강제청산 페일세이프 — 크론이 안 돌아도 장마감 전에 포지션을 정리한다.

왜 필요한가 (2026-11-19 실측)
-----------------------------
SQQQ 1주는 `force_exit_kst: "05:40"`(15:40 ET 세션 종가 청산)로 등록됐다. 그런데
클라우드 `CUBE Market Autotrade`의 마지막 사이클은 **03:32 KST**였고, 05:40에 닿는
사이클이 아예 발화하지 않았다(GitHub Actions의 `schedule` 트리거는 이 저장소에서
11-20 00:56 UTC 1회 말고는 기록이 없다 — push/수동 실행만 돌았다). 엔진에는 그 사실을
알 방법이 없었고, 청산은 다음 세션까지 미뤄졌다.

원인은 세 겹이다.
1. `scripts/cloud_cycle.py market`은 정규장이 아니면 **SKIPPED_OFF_HOURS로 즉시 끝난다** —
   늦게라도 돈 사이클은 밀린 강제청산을 집행하지 않는다.
2. `core/autotrader.run()`은 `phase != REGULAR`이면 exits를 아예 비운다 — 연체 청산을
   기록조차 하지 않는다.
3. 강제청산 시각이 마감 20분 전(05:40)이라, 사이클 하나만 빠져도 그 세션에는 기회가 없다.

이 모듈이 하는 일
-----------------
* `overdue(brackets, clock)` — 강제청산 시각(+여유 버퍼)이 지났는데 열려 있는 포지션 목록.
  정규장 여부와 무관하게 계산한다(POST_MARKET·PRE_MARKET에서도 '연체'를 안다).
* `check_gap(clock, now)` — 세션 안에서의 나이와 마지막 클라우드 사이클 시각으로
  '감시 공백'을 판정한다(`data/cloud_status.json`).
* `plan_flag(bracket)` — 강제청산을 **10분 앞당겨** 발동시키는 버퍼로, 사이클 1회 누락을
  흡수한다(`force_exit_early_min`, 기본 10 → 05:30 KST 발동).
* `record(...)` — `data/exit_failsafe.json`에 상태를 남긴다. 대시보드·10인 피드·워치독이
  같은 파일을 본다.

집행은 여전히 `core/autotrader.py` 한 곳에서만 한다. 이 모듈은 '무엇이 연체됐는지'만 정한다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core import session_time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
OUT_PATH = os.path.join(DATA, "exit_failsafe.json")
CLOUD_PATH = os.path.join(DATA, "cloud_status.json")
KST = timedelta(hours=9)

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "force_exit_early_min": 10,      # 05:40 → 05:30 발동(사이클 누락 흡수)
    "cycle_gap_warn_min": 25,        # 정규장에서 마지막 사이클이 이보다 오래되면 경고
    "cycle_gap_critical_min": 45,    # 이보다 오래되면 '감시 공백'으로 기록
}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + KST


def settings(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    block = ((policy or {}).get("risk_guardrails") or {}).get("failsafe") or {}
    if isinstance(block, dict):
        for k in cfg:
            if k in block:
                cfg[k] = block[k]
    try:
        cfg["force_exit_early_min"] = max(0, min(30, int(cfg["force_exit_early_min"])))
    except (TypeError, ValueError):
        cfg["force_exit_early_min"] = DEFAULTS["force_exit_early_min"]
    return cfg


def _shifted(clock: Dict[str, Any], hhmm: str, early_min: int = 0) -> int:
    """정책 시각(KST, EST 정규장 기준) → 이 세션의 세션 분 − 앞당김.

    강제청산은 **마감 기준** 게이트라 조기마감일에는 같이 당겨진다(05:40 → 02:40).
    """
    gate = session_time.shift_close_minutes(clock or {}, session_time.session_minutes(hhmm))
    return gate - int(early_min)


def force_due(bracket: Dict[str, Any], clock: Dict[str, Any],
              early_min: int = 0) -> bool:
    """이 브래킷의 강제청산 시각(+버퍼)이 지났는가."""
    fex = bracket.get("force_exit_kst")
    if not fex or bracket.get("force_exited"):
        return False
    now = session_time.session_minutes(session_time.kst_hhmm(clock or {}))
    gate = _shifted(clock, str(fex), early_min)
    if now < 0 or gate < 0:
        return False
    return now >= gate


def overdue(brackets: Dict[str, Any], clock: Dict[str, Any],
            policy: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """강제청산 시각이 지났는데 아직 열려 있는 포지션(정규장 여부 무관)."""
    from core import exit_rules

    cfg = settings(policy)
    out: List[Dict[str, Any]] = []
    if not cfg.get("enabled", True):
        return out
    now = session_time.session_minutes(session_time.kst_hhmm(clock or {}))
    for tk, b in (brackets or {}).items():
        if not isinstance(b, dict) or not exit_rules.is_open(b):
            continue
        fex = b.get("force_exit_kst")
        if not fex:
            continue
        try:
            early = int(b.get("force_exit_early_min", cfg["force_exit_early_min"]))
        except (TypeError, ValueError):
            early = int(cfg["force_exit_early_min"])
        gate = _shifted(clock, str(fex), max(0, min(30, early)))
        if now < 0 or gate < 0 or now < gate:
            continue
        minutes_late = now - session_time.shift_close_minutes(
            clock or {}, session_time.session_minutes(str(fex)))
        same_session = (not b.get("last_session_date")
                        or b.get("last_session_date") == (clock or {}).get("session_date"))
        out.append({
            "ticker": str(tk).upper(), "force_exit_kst": fex,
            "effective_force_exit_kst": session_time.close_gate_hhmm(clock or {}, str(fex)),
            "close_shift_min": session_time.close_shift_minutes(clock or {}),
            "minutes_late": minutes_late, "same_session": same_session,
            "qty": exit_rules.remaining_qty(b),
            "exit_rule": b.get("exit_mode") or b.get("bracket_version"),
            "phase": (clock or {}).get("phase"),
            "severity": "CRITICAL" if minutes_late >= 0 else "DUE_EARLY",
            "note": ("강제청산 시각 경과 — 정규장 밖이므로 다음 정규장 첫 사이클에서 즉시 청산"
                     if (clock or {}).get("phase") != "REGULAR" or not same_session
                     else "강제청산 시각 경과 — 즉시 청산 필요"),
        })
    return out


def cloud_gap(now: Optional[datetime] = None,
              clock: Optional[Dict[str, Any]] = None,
              policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """마지막 클라우드 시장 사이클이 얼마나 오래됐는가(감시 공백 판정)."""
    cfg = settings(policy)
    now = now or _now_kst()
    doc = {}
    try:
        with open(CLOUD_PATH, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    last = ((doc.get("market") or {}).get("last_cycle_kst")
            or (doc.get("last_run") or {}).get("at_kst") or "")
    age = None
    try:
        dt = datetime.strptime(str(last)[:19], "%Y-%m-%d %H:%M:%S")
        age = round((now - dt).total_seconds() / 60.0, 1)
    except ValueError:
        age = None
    phase = (clock or {}).get("phase")
    critical = age is not None and age >= float(cfg["cycle_gap_critical_min"])
    warn = age is not None and age >= float(cfg["cycle_gap_warn_min"])
    return {
        "last_market_cycle_kst": last or None,
        "age_min": age,
        "phase": phase,
        "warn": bool(warn and phase in ("REGULAR", "PRE_MARKET")),
        "critical": bool(critical and phase == "REGULAR"),
        "thresholds_min": {"warn": cfg["cycle_gap_warn_min"], "critical": cfg["cycle_gap_critical_min"]},
        "scheduled_triggers_reliable": False,
        "note": ("GitHub Actions schedule 트리거는 지연·누락될 수 있다(11-19 05:40 미발화 실측). "
                 "감시 공백이 critical이면 보완 경로(`scripts/exit_watchdog.py`)가 사이클을 재촉한다."),
    }


def record(brackets: Dict[str, Any], clock: Dict[str, Any],
           policy: Optional[Dict[str, Any]] = None,
           persist: bool = True) -> Dict[str, Any]:
    cfg = settings(policy)
    doc = {
        "checked_at_kst": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "phase": (clock or {}).get("phase"),
        "session_date": (clock or {}).get("session_date"),
        "force_exit_early_min": cfg["force_exit_early_min"],
        "overdue": overdue(brackets, clock, policy),
        "cloud": cloud_gap(clock=clock, policy=policy),
    }
    doc["action_required"] = bool(doc["overdue"] or doc["cloud"]["critical"])
    if persist:
        try:
            os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return doc
