"""
청산 규칙 단일 권한(source of truth) — "누가 이 포지션의 손절·청산을 정하는가".

왜 필요한가 (2026-11-19 실측)
-----------------------------
같은 SQQQ 1주에 두 개의 청산 계획이 동시에 존재했다.

    data/active_brackets.json  →  bracket_class INTRADAY · exit_mode chandelier_v2
                                  SL $45.99 · force_exit 05:40 KST (BEAR_ALPHA_HEDGE 규격)
    data/avg_anchor_plan.json  →  SL $47.46 (-1.5%) · TP1 $49.19 · force_exit null

`core/exit_rules.decide()`는 브래킷을 보고 chandelier_v2를 골랐지만,
`core/avg_anchor_engine.apply_to_brackets()`는 **이미 있는 브래킷도 사다리로 덮어쓴다**
(`sl`, `tp1`, `force_exit_kst`를 그대로 대입). 한 번 `--apply`가 돌면 장중 헤지의
05:40 강제청산이 사라지고 SL이 47.46으로 당겨진다 — 어느 쪽이 진짜인지 코드가 답하지
못하는 상태였다.

이 모듈의 답
------------
* 브래킷마다 **소유자(owner)** 를 하나 정한다.

    USER_LOCKED      사용자가 직접 지정한 레벨(예: NVDA TP 173 / SL 150). 엔진은 불변.
    SPEC:<source>    진입 신호의 브래킷 규격(core/bracket_specs)이 소유. 장중·헤지·파일럿.
    DAILY_SWING      승인된 일봉 스윙(ATR×1.5 대칭, 3세션).
    AVG_ANCHOR       평단 사다리(수동·레거시 포지션 전용).
* `exit_rule_of(owner, bracket)` → 실제로 적용할 규칙 이름.
* `can_ladder_write(ticker, bracket)` → 평단 엔진이 이 브래킷을 덮어써도 되는가.
* `conflicts(brackets, plan)` → 두 파일이 같은 종목에 다른 규칙을 말하고 있으면 목록화.
* `annotate_plan(plan, brackets)` → 사다리에 `managed_by`/`advisory_only`를 붙여,
  "이 사다리는 참고값이고 실제 청산은 브래킷 규격이 한다"를 파일 자체에 남긴다.

`scripts/cloud_cycle.py`는 매 사이클 `conflicts()`를 `data/exit_authority.json`에 남기고,
`core/autotrader.py`는 `exit_rule_of()`로 규칙을 고른다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
OUT_PATH = os.path.join(DATA, "exit_authority.json")
KST = timedelta(hours=9)

SPEC_OWNED_CLASSES = {"INTRADAY", "PILOT_C5", "HEDGE_EVENT"}
SPEC_EXIT_MODES = {"chandelier_v2", "time_exit", "inflection_v1"}
LADDER_CLASSES = {"DAILY_SWING", "MANUAL", None, ""}


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


def owner_of(ticker: str, bracket: Dict[str, Any]) -> Dict[str, Any]:
    """브래킷 하나의 청산 권한자."""
    b = bracket or {}
    if b.get("levels_locked"):
        return {"owner": "USER_LOCKED", "managed_by": "USER",
                "exit_rule": "ladder", "writable_by_ladder": False,
                "locked_levels": {"tp1": b.get("tp1"), "sl": b.get("sl")}}
    src = str(b.get("entry_source") or b.get("bracket_spec") or "").upper()
    cls = b.get("bracket_class")
    mode = b.get("exit_mode")
    if mode in SPEC_EXIT_MODES or cls in SPEC_OWNED_CLASSES:
        default_rule = ("chandelier_v2" if cls == "INTRADAY" else "time_exit")
        return {"owner": f"SPEC:{src or cls or 'INTRADAY'}", "managed_by": "BRACKET_SPEC",
                "exit_rule": mode or default_rule,
                "writable_by_ladder": False}
    if b.get("swing_hold") or b.get("bracket_version") == "swing_daily_atr15":
        return {"owner": "DAILY_SWING", "managed_by": "BRACKET_SPEC", "exit_rule": "swing_hold",
                "writable_by_ladder": False}
    return {"owner": "AVG_ANCHOR", "managed_by": "AVG_ANCHOR_PROFIT_DESK",
            "exit_rule": "ladder", "writable_by_ladder": True}


def exit_rule_of(bracket: Dict[str, Any]) -> str:
    """`core/exit_rules.decide()`가 따라야 하는 규칙 이름."""
    return owner_of("", bracket)["exit_rule"]


def can_ladder_write(ticker: str, bracket: Optional[Dict[str, Any]]) -> bool:
    """평단 엔진이 이 브래킷의 레벨을 덮어써도 되는가.

    장중·헤지·파일럿 브래킷과 사용자 잠금 레벨은 **절대** 덮어쓰지 않는다.
    """
    if not bracket:
        return True
    info = owner_of(ticker, bracket)
    return bool(info.get("writable_by_ladder"))


def _ladder_levels(lad: Dict[str, Any]) -> Dict[str, Any]:
    return {"sl": lad.get("sl"), "tp1": lad.get("tp1"), "tp2": lad.get("tp2"),
            "force_exit_kst": lad.get("force_exit_kst")}


def _bracket_levels(b: Dict[str, Any]) -> Dict[str, Any]:
    return {"sl": b.get("sl"), "tp1": b.get("tp1"), "tp2": b.get("tp2"),
            "force_exit_kst": b.get("force_exit_kst")}


def conflicts(brackets: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """브래킷과 평단 사다리가 같은 종목에 다른 규칙을 말하는 경우."""
    out: List[Dict[str, Any]] = []
    ladders = {str(l.get("ticker") or "").upper(): l for l in ((plan or {}).get("ladders") or [])}
    for tk, b in (brackets or {}).items():
        if not isinstance(b, dict) or not b.get("entry_price"):
            continue
        if b.get("carry_parking") or b.get("closed"):
            continue
        lad = ladders.get(str(tk).upper())
        if not lad:
            continue
        info = owner_of(tk, b)
        if info["owner"] == "AVG_ANCHOR":
            continue                      # 사다리가 소유자 — 충돌 아님
        diffs = {}
        for key, bl, ll in (("sl", b.get("sl"), lad.get("sl")),
                            ("force_exit_kst", b.get("force_exit_kst"), lad.get("force_exit_kst"))):
            try:
                same = bl is not None and ll is not None and abs(float(bl) - float(ll)) < 1e-6
            except (TypeError, ValueError):
                same = (bl or None) == (ll or None)
            if not same and (bl is not None or ll is not None):
                diffs[key] = {"bracket": bl, "ladder": ll}
        if diffs:
            out.append({"ticker": str(tk).upper(), "owner": info["owner"],
                        "managed_by": info["managed_by"], "exit_rule": info["exit_rule"],
                        "diffs": diffs,
                        "resolution": (f"{info['owner']} 우선 — 평단 사다리는 참고값(advisory). "
                                       "브래킷 레벨로 청산한다."),
                        "severity": ("HIGH" if "force_exit_kst" in diffs else "MEDIUM")})
    return out


def annotate_plan(plan: Dict[str, Any], brackets: Optional[Dict[str, Any]],
                  persist: bool = True) -> Dict[str, Any]:
    """사다리마다 소유자와 '참고값' 여부를 남긴다(파일이 스스로 답하게)."""
    brackets = brackets or {}
    annotated: List[Dict[str, Any]] = []
    conflicts_found: List[Dict[str, Any]] = []
    for lad in (plan.get("ladders") or []):
        tk = str(lad.get("ticker") or "").upper()
        b = brackets.get(tk) or {}
        info = owner_of(tk, b) if b else {"owner": "AVG_ANCHOR", "managed_by": "AVG_ANCHOR_PROFIT_DESK",
                                         "exit_rule": "ladder", "writable_by_ladder": True}
        lad["managed_by"] = info["managed_by"]
        lad["owner"] = info["owner"]
        lad["exit_rule"] = info["exit_rule"]
        lad["advisory_only"] = not bool(info.get("writable_by_ladder")) and not b.get("levels_locked")
        if b:
            lad["authoritative_levels"] = _bracket_levels(b) if lad["advisory_only"] or b.get("levels_locked") else None
        annotated.append(lad)
    plan["ladders"] = annotated
    conflicts_found = conflicts(brackets, plan)
    plan["exit_authority"] = {
        "checked_at_kst": _now_kst(),
        "ladders": {l["ticker"]: {"owner": l.get("owner"), "exit_rule": l.get("exit_rule"),
                                  "advisory_only": l.get("advisory_only")} for l in annotated},
        "conflicts": conflicts_found,
        "rule": ("한 포지션의 청산 규칙은 하나다. 장중·헤지·파일럿 브래킷(SPEC)과 사용자 "
                 "잠금 레벨은 평단 사다리가 덮어쓰지 않는다."),
    }
    if persist:
        try:
            os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                json.dump(plan["exit_authority"], f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return plan


def report(brackets: Dict[str, Any], plan: Optional[Dict[str, Any]] = None,
           persist: bool = True) -> Dict[str, Any]:
    """사이클 보고용 요약(주문 없음)."""
    owners = {}
    for tk, b in (brackets or {}).items():
        if not isinstance(b, dict) or b.get("carry_parking"):
            continue
        owners[tk] = owner_of(tk, b)
    conf = conflicts(brackets, plan)
    doc = {"checked_at_kst": _now_kst(), "owners": owners, "conflicts": conf,
           "ok": not conf,
           "note": "청산 규칙 소유자 단일화 — 충돌 0이 정상."}
    if persist:
        try:
            os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return doc
