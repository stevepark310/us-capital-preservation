"""
브래킷 규격 분리 — 일봉 스윙 / 장중 / C5 파일럿이 같은 규격을 쓰지 않게 한다.

왜 필요한가 (2026-11-18 실측)
----------------------------
기존 `_new_bracket`은 진입 채널과 무관하게 `chandelier_v2` 하나를 썼다. 그래서
- 일봉 채널(DIP_SWING_DAILY)은 일봉 ATR%(SOXL 19.8~25.9%)를 그대로 넘겨 SL 15%(클램프
  상한) · 트레일 0.9×ATR ≈ 23%가 되었고, 10분봉 노이즈에는 전혀 반응하지 않는
  사실상 "3세션 방치" 브래킷이 됐다.
- 장중 채널(DIP_SWING_INTRADAY)은 5분봉 ATR 1.5~2.6% → SL 1.5% 클램프 · 트레일 1.9%로
  너무 좁아 3~9% 스윙의 앞 1/3에서 끊겼다(11-17 +7.7% 구간 중 +3.89%만 확보).
- C5 파일럿은 손절 3×ATR · 15:40 ET 시간청산이라는 **다른 규격**을 요구한다.

이 모듈은 `entry_source` → 규격 한 벌을 돌려주는 단일 표다. 규격 값은
`config/swing_approval.json`의 `autonomous_execution.bracket_specs`가 정본이며,
여기 값은 파일이 없거나 키가 빠졌을 때의 기본값이다.

규격 요약
---------
| entry_source            | class        | ATR 기준   | 손절                | 익절/청산                          |
|-------------------------|--------------|-----------|---------------------|-----------------------------------|
| DIP_SWING_DAILY         | DAILY_SWING  | 일봉 ATR%  | 1.5×ATR (3~15%)     | +1.5×ATR (3~20%) · 3세션 시간청산   |
| DIP_SWING_INTRADAY      | INTRADAY     | 5분봉 ATR% | 0.75×ATR (1.5~15%)  | 트레일 0.9×ATR · 05:40 세션청산     |
| BEAR_ALPHA_HEDGE        | INTRADAY     | 5분봉 ATR% | 0.75×ATR (1.5~15%)  | 트레일 0.9×ATR · 05:40 세션청산     |
| C5_OPEN30_DIRECTION     | PILOT_C5     | 5분봉 ATR% | 3.0×ATR (2~15%)     | 05:40 시간청산(트레일 없음)         |
| NVDA_EARNINGS_GAP_SOXS  | HEDGE_EVENT  | —          | 재난손절 8%         | 05:55 시간청산                      |

**장중 트레일은 0.9×ATR을 유지한다(2026-11-18 12:05 지시).** 일괄 1.5×ATR로 올리면
DIP_INTRADAY 기준선이 5거래일에 −$2.72 → −$7.75(60일 −$31.29 → −$44.80)로 악화됐다 —
장중 채널은 5분봉 ATR 1.5~2.6%에서 0.9×ATR = 1.4~2.3%가 이미 스윙 크기에 맞다.
넓히는 것은 **일봉 스윙 채널에만**(ATR×1.5 대칭 브래킷) 적용한다. 장중 값은
`data/strategy_params.json trail_atr_mult`(0.9, 클램프 0.60~1.20)에서 온다.

세션청산(`session_close_exit_kst`)은 장중 규격 전용이다 — 장중 브래킷이 다음 세션으로
넘어가면 "장중"이라는 이름과 규격이 어긋난다. 기존에 등록된 브래킷은 자기 필드 값을
그대로 쓰므로(exit_rules가 브래킷 필드를 읽는다) 이 표는 **신규 진입분에만** 적용된다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 신규 진입 기본 규격 (config가 정본, 이 표는 폴백)
DEFAULT_SPECS: Dict[str, Dict[str, Any]] = {
    "DIP_SWING_DAILY": {
        "bracket_class": "DAILY_SWING",
        "atr_basis": "daily",
        "exit_mode": "swing_hold",          # 승인된 DIP_SWING 규격: ATR×1.5 대칭 + 3세션
        "initial_sl_atr_mult": 1.5,
        "sl_min_pct": 3.0,
        "sl_max_pct": 15.0,
        "tp_atr_mult": 1.5,
        "tp_min_pct": 3.0,
        "tp_max_pct": 20.0,
        "max_hold_sessions": 3,
        "sl_active_after_kst": "23:45",
    },
    "DIP_SWING_INTRADAY": {
        "bracket_class": "INTRADAY",
        "atr_basis": "intraday",
        "exit_mode": "chandelier_v2",
        "initial_sl_atr_mult": 0.75,
        "sl_min_pct": 1.5,
        "sl_max_pct": 15.0,
        "trail_atr_mult": 0.9,              # 2026-11-18 12:05 지시 — 장중은 0.9 유지
        "be_after_atr": 1.0,
        "max_hold_sessions": 3,
        "session_close_exit_kst": "05:40",
        "sl_active_after_kst": "23:45",
    },
    "BEAR_ALPHA_HEDGE": {
        "bracket_class": "INTRADAY",
        "atr_basis": "intraday",
        "exit_mode": "chandelier_v2",
        "initial_sl_atr_mult": 0.75,
        "sl_min_pct": 1.5,
        "sl_max_pct": 15.0,
        "trail_atr_mult": 0.9,
        "be_after_atr": 1.0,
        "max_hold_sessions": 3,
        "session_close_exit_kst": "05:40",
        "sl_active_after_kst": "23:45",
    },
    "C5_OPEN30_DIRECTION": {
        "bracket_class": "PILOT_C5",
        "atr_basis": "intraday",
        "exit_mode": "time_exit",
        "initial_sl_atr_mult": 3.0,         # C5 A/B: 손절 2~5×ATR 전 구간 플러스
        "sl_min_pct": 2.0,
        "sl_max_pct": 15.0,
        "trail_atr_mult": None,             # 트레일 없음 — 15:40 ET 종가 청산
        "time_exit_kst": "05:40",
        "max_hold_sessions": 1,
        "sl_active_after_kst": "00:00",
    },
    "NVDA_EARNINGS_GAP_SOXS": {
        "bracket_class": "HEDGE_EVENT",
        "atr_basis": "intraday",
        "exit_mode": "time_exit",
        "initial_sl_atr_mult": None,        # 재난손절은 신호(disaster_stop_pct)가 정한다
        "time_exit_kst": "05:55",
        "max_hold_sessions": 1,
        "sl_active_after_kst": "23:30",
    },
}

# 규격에 없으면 이 클래스로 취급한다(기존 동작 = 장중 샹들리에)
DEFAULT_CLASS = "INTRADAY"


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def all_specs(override: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """기본표 + config 오버라이드 병합본."""
    out = {k: dict(v) for k, v in DEFAULT_SPECS.items()}
    for src, spec in (override or {}).items():
        if not isinstance(spec, dict):
            continue
        out[src] = _merge(out.get(src, {}), spec)
    return out


def spec_for(source: Optional[str],
             override: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """진입 신호 → 브래킷 규격. 모르는 신호는 장중 규격으로 처리한다."""
    key = (source or "").upper()
    specs = all_specs(override)
    if key in specs:
        spec = dict(specs[key])
        spec["resolved_from"] = key
        return spec
    intraday = dict(specs["DIP_SWING_INTRADAY"])
    intraday["resolved_from"] = f"DEFAULT:{DEFAULT_CLASS}"
    return intraday


def _clamped(mult: Optional[float], atr_pct: Optional[float],
             lo: Optional[float], hi: Optional[float]) -> Optional[float]:
    if mult is None:
        return None
    atr = float(atr_pct or 0.0)
    if atr <= 0:
        atr = 6.0
    pct = abs(float(mult)) * atr
    if lo is not None:
        pct = max(pct, float(lo))
    if hi is not None:
        pct = min(pct, float(hi))
    return round(pct, 3)


def sl_pct(spec: Dict[str, Any], atr_pct: Optional[float]) -> Optional[float]:
    """규격의 손절 폭(%). None이면 호출한 쪽이 정한다(예: 재난손절)."""
    return _clamped(spec.get("initial_sl_atr_mult"), atr_pct,
                    spec.get("sl_min_pct"), spec.get("sl_max_pct"))


def tp_pct(spec: Dict[str, Any], atr_pct: Optional[float]) -> Optional[float]:
    """규격의 익절 폭(%). swing_hold 규격에서만 쓰인다."""
    return _clamped(spec.get("tp_atr_mult"), atr_pct,
                    spec.get("tp_min_pct"), spec.get("tp_max_pct"))
