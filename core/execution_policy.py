"""
Autonomy policy — 동훈님의 무인 집행 위임(2026-11-17 12:02)과 하드 가드레일.

`config/swing_approval.json`의 `autonomous_execution` 블록이 정본이다. 이 모듈은
그 값을 읽어 ① 1회 진입 예산 ② 일일 손실 서킷브레이커 ③ 세션 시간 게이트를
계산한다. 모든 계산은 '작은 쪽이 이긴다'(가장 보수적인 상한)로 결합하며,
코드에 박아 둔 절대 상한(SAFE_CAPS)을 정책 파일이 넘어설 수 없다.

위임의 경계
-----------
- 위임된 것: 신호 판단 → 매수/매도 → 브래킷 등록·청산을 사용자 확인 없이 집행.
- 2026-11-18 11:37 추가 승인(`C5 파일럿 + 구조 개선 3건`): ① 숏 게이트 확인 개수
  2-of-2 → 1-of-1(VIX ≥ 24 하한은 고정) ③ 일봉/장중 브래킷 규격 분리(장중 트레일 0.9×ATR
  유지), C5 개장30분 방향 파일럿($150/일 1회).  ② DIP의 RVOL 필수 → 가점은 같은 날 14:25
  동훈님 지시로 **원복** — RVOL ≥ 1.0은 다시 필수 조건이다(승인 조건이 전략 파라미터의 하한).
- 위임되지 않은 것: NVDA `levels_locked`(TP $173.0 / SL $150.0) 변경, 물타기,
  스몰캡 급등주, **VIX 하한 24.0 미만의 숏 허용**, 신규 종목 편입.
  이 네 가지는 정책 파일로도 켤 수 없게 코드에서 막는다.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "swing_approval.json")
GUARDRAILS_PATH = os.path.join(BASE_DIR, "config", "risk_guardrails.json")
HALT_FILE = os.path.join(BASE_DIR, "data", "autotrader_halt.json")

# 정책 파일이 무엇을 쓰든 넘을 수 없는 절대 상한 (계좌 $883 · 2026-11-18 예산 상향 반영)
SAFE_CAPS: Dict[str, float] = {
    "entry_budget_cap_usd": 220.0,        # 1회 진입 명목 상한 (사용자 지시 2026-11-18 10:30)
    "entry_budget_pct_of_cash": 40.0,     # 가용 현금 대비
    "entry_budget_pct_of_equity": 25.0,   # 총자산 대비
    "entry_budget_floor_usd": 150.0,      # 변동성 감쇠가 내려갈 수 있는 최저 집행 규모
    "risk_per_trade_pct": 3.0,            # 손절까지 감수하는 자산 비율
    "max_open_positions": 4,
    "max_entries_per_session": 4,
    "daily_realized_loss_limit_pct": 3.0,
    "daily_realized_loss_limit_usd": 30.0,
    # 1주 단가 상한 — 정수주 단위 계좌라 $220 예산 상한에 걸리는 고가 대형주를 열어 준다
    # (동훈님 지시 2026-11-27 13:37: "단가 상한을 가용 현금 기준 $700대 대형주까지").
    "max_unit_price_usd": 750.0,
    "single_share_cash_reserve_usd": 40.0,
}

# 사용자가 잠근 레벨 — 어떤 경로로도 변경 금지
LOCKED_LEVELS: Dict[str, Dict[str, float]] = {"NVDA": {"tp": 173.0, "sl": 150.0}}
# 실전 배제 유지 (11-13 지시)
BANNED_TICKERS = {"ENVX", "IBRX", "SOUN"}

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "ask_before_trade": True,
    "universe": ["SOXL", "TQQQ"],
    "guardrails": {
        "risk_per_trade_pct": 3.0,
        "entry_budget_pct_of_cash": 35.0,
        "entry_budget_pct_of_equity": 25.0,
        "entry_budget_cap_usd": 220.0,
        "entry_budget_floor_usd": 150.0,
        "cash_reserve_floor_usd": 150.0,
        "max_open_positions": 4,
        "max_entries_per_session": 3,
        "max_orders_per_session": 8,
        "daily_realized_loss_limit_usd": 25.0,
        "daily_realized_loss_limit_pct": 3.0,
        "ticker_cooldown_min": 20,
        "duplicate_order_window_min": 10,
        "order_timeout_sec": 300,
        "opening_blackout_kst": "23:30-00:00",
        "no_new_entry_after_kst": "04:30",
        "force_exit_kst": "05:40",
        # 1주 단가 상한(2026-11-27 지시) — 이 값 이하 1주는 '예산 부족'으로 거부하지 않고,
        # 가용 현금 − single_share_cash_reserve 안에서 1주만 산다. 진입 가능 여부는
        # autotrader가 캔들마다 `single_share_budget()`로 확인한다.
        "max_unit_price_usd": 750.0,
        "single_share_cash_reserve_usd": 40.0,
        "min_price_usd": 3.0,
        "vix_riskoff_scale": {"24.0": 0.5, "28.0": 0.0},
        "no_averaging_down": True,
    },
    "execution": {"order_type": "limit", "buy_limit_offset_bps": 20,
                  "sell_limit_offset_bps": -30, "price_round": 2},
    "execution_channel": {"primary": "github_actions", "workflow": "nh_trade_dispatch.yml",
                          "repo": "stevepark310/us-capital-preservation"},
    "safety": {"single_writer_lease_min": 15, "lock_stale_min": 12,
               "kill_switch_file": "data/autotrader_halt.json"},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    """정책 로드 — 파일이 없거나 형식이 깨져도 안전한 기본값(비활성)으로."""
    try:
        with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        block = cfg.get("autonomous_execution") or {}
        policy = _deep_merge(DEFAULTS, block)
        policy["cautious_mode"] = cfg.get("cautious_mode") or {}
        policy["c5_pilot"] = block.get("c5_pilot") or {}
        policy["bracket_specs"] = block.get("bracket_specs") or {}
        policy["bear_alpha_gate"] = block.get("bear_alpha_gate") or {}
        # 2026-11-20 재발방지 대책 — 손실 하드캡·시세 정합성·페일세이프 (별도 정본 파일)
        try:
            with open(GUARDRAILS_PATH, "r", encoding="utf-8") as f:
                policy["risk_guardrails"] = json.load(f)
        except Exception as exc:
            policy["risk_guardrails"] = {}
            policy["risk_guardrails_error"] = type(exc).__name__
        policy["swing_approval"] = {
            "status": cfg.get("overnight_status"),
            "universe": cfg.get("universe"),
            "entry_conditions": cfg.get("entry_conditions"),
            "exit_rules": cfg.get("exit_rules"),
            "risk_limits": cfg.get("risk_limits"),
        }
        return policy
    except Exception as e:
        policy = dict(DEFAULTS)
        policy["load_error"] = str(e)
        return policy


def guardrails(policy: Dict[str, Any]) -> Dict[str, Any]:
    g = dict(policy.get("guardrails") or {})
    for key, cap in SAFE_CAPS.items():
        if key in g:
            try:
                g[key] = min(float(g[key]), float(cap))
            except (TypeError, ValueError):
                g[key] = float(cap)
    return g


def loss_limit_usd(equity: float, policy: Dict[str, Any]) -> float:
    """일일 실현손실 한도 — 금액 한도와 자산 % 중 작은 쪽(엄격한 쪽)."""
    g = guardrails(policy)
    pct_part = abs(float(g.get("daily_realized_loss_limit_pct", 3.0))) / 100.0 * max(float(equity or 0), 0.0)
    usd_part = abs(float(g.get("daily_realized_loss_limit_usd", 25.0)))
    if pct_part <= 0:
        return usd_part
    return round(min(pct_part, usd_part), 2)


def vix_scale(vix: Optional[float], policy: Dict[str, Any]) -> float:
    """VIX 구간별 리스크 축소 계수(0이면 신규 진입 전면 금지)."""
    try:
        v = float(vix) if vix is not None else 0.0
    except (TypeError, ValueError):
        v = 0.0
    table = (policy.get("guardrails") or {}).get("vix_riskoff_scale") or {}
    scale = 1.0
    for level, factor in table.items():
        try:
            if v >= float(level):
                scale = min(scale, float(factor))
        except (TypeError, ValueError):
            continue
    return max(0.0, min(1.0, scale))


def entry_budget(equity: float, cash: float, policy: Dict[str, Any],
                 vol_scale: float = 1.0) -> Dict[str, Any]:
    """1회 진입 예산 — 현금/자산/절대상한/현금보존 하한을 모두 만족하는 최소값.

    변동성 감쇠(VIX × 전략 계수)는 **최저 집행 규모(`entry_budget_floor_usd`) 아래로는
    내리지 않는다**(2026-11-18 교착 해소). 감쇠가 예산을 종목가 아래로 밀어 내면
    신호가 나와도 정수주 0주가 되어 매수가 영구히 막혔기 때문이다(11-17 14시간 0건).

    위험 통제는 예산 상한이 아니라 ① 거래당 리스크 한도(`risk_per_trade_pct`)
    ② 일일 실현손실 서킷브레이커 ③ 보유 종목·세션 진입 건수로 건다. 감쇠 계수는
    이제 `floor ~ 상한` 사이의 폭을 조절한다(VIX ≥ 28은 계수 0 = 진입 전면 금지 유지).

    반환값의 `binding`은 어떤 제약이 예산을 결정했는지 남긴다(보고·로그용).
    """
    g = guardrails(policy)
    equity = max(float(equity or 0), 0.0)
    cash = max(float(cash or 0), 0.0)
    candidates = {
        "cash_pct": cash * float(g.get("entry_budget_pct_of_cash", 35.0)) / 100.0,
        "equity_pct": equity * float(g.get("entry_budget_pct_of_equity", 25.0)) / 100.0,
        "abs_cap": float(g.get("entry_budget_cap_usd", 220.0)),
        "cash_after_reserve": cash - float(g.get("cash_reserve_floor_usd", 150.0)),
    }
    usable = {k: v for k, v in candidates.items() if v is not None}
    binding = min(usable, key=usable.get) if usable else "none"
    raw = max(0.0, min(usable.values())) if usable else 0.0
    scale = max(0.0, min(1.0, float(vol_scale)))
    floor = max(0.0, float(g.get("entry_budget_floor_usd", 150.0)))
    # 하한은 어떤 상한도 넘지 않는다 — 상한이 작으면 그 상한이 곧 예산이다.
    floor_effective = min(floor, raw)
    decayed = raw * scale
    if scale <= 0.0:
        budget = 0.0          # VIX 리스크오프는 하한으로도 되살리지 않는다
    else:
        budget = max(decayed, floor_effective)
    return {
        "budget_usd": round(budget, 2),
        "binding": binding,
        "raw_usd": round(raw, 2),
        "decayed_usd": round(decayed, 2),
        "components": {k: round(v, 2) for k, v in candidates.items()},
        "vol_scale": round(scale, 3),
        "floor_applied": bool(scale > 0.0 and decayed < floor_effective - 1e-9),
        "below_floor": budget < floor and scale > 0.0,
        "floor_usd": floor,
    }


def unit_price_cap(policy: Optional[Dict[str, Any]] = None, cash: Optional[float] = None) -> Dict[str, Any]:
    """1주 단가 상한 — 정책 상한(`max_unit_price_usd`)과 가용 현금으로 동시에 제한.

    왜 필요한가 (2026-11-27 13:37 동훈님 지시)
    ----------------------------------------
    "동적 유니버스 단가 상한을 가용 현금 기준 $700대 대형주까지 넓혀 주세요."
    정수주 계좌($881)에서 $220 예산 상한은 AAPL·MSFT·META 같은 대형주를 전부
    `affordable=false`로 밀어냈다. 1주 단가 상한을 `min(정책 상한, 가용 현금 − 안전마진)`
    으로 계산해, 현금이 줄면 상한도 같이 줄어들게 한다(고정 $750이 아니라 동적 연동).

    반환값
      cap_usd          이번 판단에 쓸 1주 상한
      hard_cap_usd     정책·SAFE_CAPS가 정한 절대 상한
      usable_cash_usd  현금 − single_share_cash_reserve
      binding          어떤 제약이 상한을 정했는지(cash / policy)
    """
    g = guardrails(policy or {})
    hard = float(g.get("max_unit_price_usd", SAFE_CAPS["max_unit_price_usd"]))
    hard = min(hard, float(SAFE_CAPS["max_unit_price_usd"]))
    reserve = float(g.get("single_share_cash_reserve_usd", 40.0))
    out: Dict[str, Any] = {"hard_cap_usd": round(hard, 2), "reserve_usd": round(reserve, 2),
                           "cash_usd": None, "usable_cash_usd": None,
                           "cap_usd": round(hard, 2), "binding": "policy"}
    if cash is None:
        return out
    usable = max(0.0, float(cash) - reserve)
    out.update({"cash_usd": round(float(cash), 2), "usable_cash_usd": round(usable, 2)})
    if usable < hard:
        out.update({"cap_usd": round(usable, 2), "binding": "cash"})
    return out


def single_share_budget(price: float, cash: float,
                        policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """1주만 사는 경우의 예산 — `entry_budget`을 넘어도 1주라면 허용할지 판정.

    1회 예산($220)은 '여러 주를 살 때의 상한'이다. $700짜리 1주는 예산을 넘지만
    그 종목을 사려면 1주가 최소 단위이므로, 다음 세 조건을 모두 만족할 때만
    예산을 그 1주 가격으로 올린다(부분 매수·분할 매수 없음).
      ① 1주 가격 ≤ 1주 단가 상한(정책 상한 ∩ 가용 현금 − 안전마진)
      ② 매수 후 현금이 `single_share_cash_reserve_usd` 이상 남는다
      ③ 예산 부족이 유일한 거부 사유다(다른 가드레일은 그대로 통과해야 한다)
    """
    try:
        px = float(price or 0.0)
        ca = float(cash or 0.0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "가격·현금 확인 불가", "budget_usd": 0.0}
    if px <= 0:
        return {"ok": False, "reason": "가격 확인 불가", "budget_usd": 0.0}
    cap = unit_price_cap(policy, ca)
    if px > cap["cap_usd"] + 1e-9:
        return {"ok": False, "reason": (f"1주 ${px:,.2f} > 단가 상한 ${cap['cap_usd']:,.2f}"
                                        f"({cap['binding']})"),
                "budget_usd": 0.0, "cap": cap}
    return {"ok": True, "reason": (f"1주 ${px:,.2f} ≤ 단가 상한 ${cap['cap_usd']:,.2f}"
                                   f"({cap['binding']}) — 1주 단위 예산 허용"),
            "budget_usd": round(px, 2), "cap": cap,
            "reserve_usd": cap["reserve_usd"],
            "cash_after_usd": round(ca - px, 2)}


def halt_file_active(policy: Optional[Dict[str, Any]] = None) -> bool:
    """비상 정지 파일(data/autotrader_halt.json)이 있으면 신규 매수 중단."""
    rel = ((policy or {}).get("safety") or {}).get("kill_switch_file") or "data/autotrader_halt.json"
    path = rel if os.path.isabs(rel) else os.path.join(BASE_DIR, rel)
    return os.path.exists(path)


def allowed_ticker(ticker: str, policy: Dict[str, Any]) -> bool:
    t = (ticker or "").upper()
    if t in BANNED_TICKERS:
        return False
    return t in {x.upper() for x in (policy.get("universe") or [])}


def locked_levels(ticker: str) -> Optional[Dict[str, float]]:
    return LOCKED_LEVELS.get((ticker or "").upper())


def describe(policy: Dict[str, Any], equity: float, cash: float,
             vix: Optional[float] = None, extra_scale: float = 1.0) -> Dict[str, Any]:
    """상태 보고용 요약(가드레일 수치와 현재 예산).

    `extra_scale`은 전략 엔진의 변동성 계수(`data/strategy_params.json` risk_scale)다.
    실제 진입 예산과 보고 숫자가 갈라지지 않도록 호출한 쪽이 넘긴다.
    """
    g = guardrails(policy)
    scale = vix_scale(vix, policy)
    total = scale * max(0.0, min(1.0, float(extra_scale or 1.0)))
    budget = entry_budget(equity, cash, policy, total)
    return {
        "enabled": bool(policy.get("enabled")),
        "ask_before_trade": bool(policy.get("ask_before_trade", True)),
        "guardrails": g,
        "daily_loss_limit_usd": loss_limit_usd(equity, policy),
        "vix": vix,
        "vix_scale": scale,
        "strategy_risk_scale": round(float(extra_scale or 1.0), 3),
        "budget_scale_total": round(total, 3),
        "entry_budget": budget,
        "unit_price": unit_price_cap(policy, cash),
        "halt_file": halt_file_active(policy),
        "universe": policy.get("universe"),
    }
