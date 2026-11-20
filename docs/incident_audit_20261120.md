# 2026-11-17~19 실전 손실 사고 감사 · 재발방지 대책

작성: 2026-11-20 KST · 근거는 전부 저장소 실측 파일·로그·GitHub Actions 기록이다(추정 없음).

## 1. 사고 개요

| 세션(ET) | 종목 | 진입 | 청산 | 손익 | 손익% | 청산 사유 |
|---|---|---|---|---|---|---|
| 11-16 | SOXL 2주 | $50.43 | $47.46 | **-$5.94** | -5.89% | STOP (계획 SL $49.17) |
| 11-16 | TQQQ 2주 | $54.00 | $52.76 | **-$2.49** | -2.31% | STOP (계획 SL $52.87) |
| 11-13 | NVDA 1주 | $173.30 | $149.20 | **-$24.10** | -13.91% | STOP (사용자 잠금 SL $150.00) |

- 3일 실현 합계 **-$32.53**, 누적 실현 -$22.25 (10전 5승 5패), 총자산 **$877.98**
  (초기 $900.18 대비 -2.47%). 출처: `data/live_portfolio.json`, `data/strategy_trade_ledger.json`,
  `scripts/settle_20261119_close.py`(NH Open API 실측 run 44614421600).
- 체결 시각(KST): TQQQ 11-18 00:32:37 · SOXL 11-18 01:22:27 · NVDA 11-20 03:40:48.

## 2. 원인 — 7개, 모두 파일·로그로 확인

### ① 손절이 브로커에 걸려 있지 않다 — 10분 폴링 + 체결 지연
엔진은 보유 포지션을 10분 사이클에서 평가해 **그때 지정가 매도를 내는** 구조다(상주 스톱주문 없음).
그 사이클이 불규칙하면 손절선을 지나쳐서야 판다.

- TQQQ: 00:02:20 사이클이 $52.87(＝손절선)을 감지 → 지정가 52.71 → 00:32 체결 52.76 (추가 -0.21%).
- SOXL: 00:33:03 사이클에서 이미 **$47.60** → 47.46 체결. 손절선 49.17 대비 **-3.48%p**를 더 잃었다
  (2주 합 -$3.42 = SOXL 손실 -$5.94의 58%).
- `data/autotrader_log.jsonl`의 EXECUTE 간격: 00:02 → 00:33(31분) → 00:54 → 01:22 → 01:26 → 01:55
  → 02:25 → **04:25(2시간 공백)** → 04:56 → 05:26 → 05:55. 10분 크론인데 21~120분 간격이다.

### ② 변동성을 무시한 사다리 손절 폭
`AVG_ANCHOR_PROFIT_DESK`의 SL은 ATR 배수가 아니라 **1.5~2.5% 고정 밴드**로 클램프된다
(`core/avg_anchor_engine.PARAMS`). 진입 당시 ATR 대비:

| 종목 | ATR% | 적용 SL | ATR 배수 |
|---|---|---|---|
| SOXL | 25.91% | -2.5% | **0.10×ATR** |
| TQQQ | 8.40% | -2.1% | 0.25×ATR |

같은 계좌의 DIP_SWING 규격은 1.5×ATR(클램프 3~15%)다 — 두 규격의 손절 폭이 **15배** 차이 난다.
SOXL은 하루 평균 변동(25.9%)의 1/10에서 잘렸으니 노이즈 손절이 구조적으로 확정된 셈이다.

### ③ 레짐(변동성·추세) 게이트 없는 진입
11-17 01:15 KST 진입 시점 VIX는 22.9에서 오르는 중이었고, 같은 주 VIX는 24.84(11-17) →
27.14~28.99(11-19)로 갔다. 진입 경로(사다리·수동 체결)에는 VIX·추세 확인이 없었다.
`vix_riskoff_scale`은 무인 엔진의 신규 진입에만 적용된다.

### ④ VIX 단일 소스 — 28 경계에서 판정이 뒤집힌다
- 03:32 KST `BEAR_ALPHA_HEDGE` 발동 근거: **VIX 27.92**(브래킷 note·`daily_trading_calendar.json`).
- 06:05 KST `avg_anchor_plan.json` 재생성: **VIX 27.14**.
- 같은 세션 `^VIX` 일봉: 고가 **28.99**, 전일 종가 24.56(`data/market_snapshot.json`).
- 승인된 계수표는 `24.0 → 0.5`, `28.0 → 0.0`(`config/swing_approval.json`). 즉 27.x와 28.99는
  **예산 0.5배 vs 신규 진입 전면 금지**로 갈린다.
- 원인: `core/market_snapshot._intraday_last()`의 수집 대상이 `EQUITY_UNIVERSE`뿐이라 VIX의 장중
  시계열이 없다. `core/entry_signals.bear_alpha_gate()`와 `execution_policy.vix_scale()`은
  `macro.vix.last` 한 값만 본다. 소스 간 교차검증·stale 판정 코드가 없었다.
- ※ 브리프에 적힌 "VIX 28.44"는 이 저장소 어디에도 기록이 없다. 기록으로 확인되는 값은
  27.92 / 27.14 / 28.99 셋이다. 28.44를 근거로 쓰지 않는다.

### ⑤ 포지션 단위 손실 상한이 없다 (NVDA -13.91%)
- 진입 시점 리스크: 1주 × ($173.30 − $150.00) = $23.30 = 자산의 **2.65%**로, 승인된 건당
  리스크 3% 안이다 → 가드레일이 막지 못했다.
- 손절선 -13.44%는 사용자가 직접 지정한 잠금 레벨이라 엔진이 존중했고, **손실 폭을 자르는
  장치가 따로 없었다.** 일일 손실 서킷(-$25/3%)은 *실현된 뒤*에만 작동한다.
- 실적(11-18 06:20 KST)을 포지션째로 통과했다. `levels_locked` 브래킷은 강제청산에서 제외됐다
  (`avg_anchor_plan.guards.nvda_levels_locked`, `bracket_specs`).

### ⑥ 청산 규칙 이중화 (SQQQ) — 잠복 충돌
같은 SQQQ 1주에 두 개의 서로 다른 규칙이 동시에 존재했다.

| 출처 | SL | 강제청산 |
|---|---|---|
| `data/active_brackets.json` (BEAR_ALPHA_HEDGE, chandelier_v2) | $45.99 | **05:40 KST** |
| `data/avg_anchor_plan.json` (평단 사다리) | $47.46 | null |

`core/avg_anchor_engine.apply_to_brackets()`는 **기존 브래킷도 사다리 값으로 덮어썼다**
(`sl`·`tp1`·`force_exit_kst`). 한 번 `--apply`가 돌면 장중 헤지의 05:40 룰이 사라지고 SL이
47.46으로 당겨진다. 이번엔 `--apply`가 돌지 않아 실제 사고로 이어지지 않았지만, 실행 1회 거리에
있던 결함이다.

### ⑦ 강제청산 사이클이 아예 발화하지 않았다 (05:40 미집행)
- `CUBE Market Autotrade (10m)` 실행 이력은 **11-19 세션 동안 schedule 0건**이다:
  18:23Z(push, 실패) · 18:28Z·18:30Z(push, `live_execution_gate` 활성화 커밋이 트리거) ·
  16:28Z(수동)뿐이고, 05:40 KST(=20:40Z)에 닿는 실행이 없다. 워크플로의 schedule 실행은
  **11-20 00:56:41Z(09:56 KST) 1건**이 전부인데, 그 시각은 정규장 밖이라 `SKIPPED_OFF_HOURS`로
  끝났다 — "10분 크론"이 실제로는 세션 안에서 거의 발화하지 않는다(`gh run list --workflow`,
  `gh api .../actions/workflows` state=active).
- 05:40에 닿는 사이클이 없었고, 늦게라도 돈 사이클은 도움이 안 됐다:
  `cloud_cycle.market_cycle()`은 정규장이 아니면 `SKIPPED_OFF_HOURS`로 즉시 끝나고,
  `autotrader.run()`은 `phase != REGULAR`이면 exits를 비운다 → **연체 청산을 기록조차 하지 않는다.**
- 결과: SQQQ는 11-20 05:40 강제청산 룰을 어기고 지금(10:08 KST)까지 보유 중이다
  (평가 +$1.50, 종가 $49.68). `data/exit_failsafe.json`에 `CRITICAL`로 기록됐다.

## 3. 구현한 재발방지 대책

| # | 대책 | 파일 | 핵심 함수 | 테스트 |
|---|---|---|---|---|
| 1 | 시세·VIX 정합성 fail-closed | `core/live_integrity.py` (신규), `config/risk_guardrails.json` | `vix_view` · `price_view` · `price_for` · `evaluate` · `entry_gate` | `tests/test_live_integrity.py` (10) |
| 1-b | VIX ≥ 28 신규 롱 하드 차단 | `core/autotrader.py::entries` | `live_integrity.entry_gate` + `vix_scale(보수적 VIX)` | `test_autotrader.py`, `test_live_integrity.py` |
| 2 | 포지션 손실 하드캡 (-8% / -$25) | `core/risk_guard.py` (신규) | `check_position` · `cap_sl_pct` | `tests/test_risk_guard.py` (12) |
| 2-b | 이벤트(실적) 리스크 필터 | `core/risk_guard.py` | `event_risk` (전 24h / 후 6h) | `test_risk_guard.py` |
| 3 | 청산 규칙 단일화(SSOT) | `core/exit_authority.py` (신규), `core/avg_anchor_engine.py` | `owner_of` · `can_ladder_write` · `conflicts` · `annotate_plan` · `apply_to_brackets` | `tests/test_exit_authority.py` (8) |
| 4 | 강제청산 페일세이프 | `core/exit_failsafe.py`, `scripts/exit_watchdog.py` (신규), `scripts/cloud_cycle.py`, `core/autotrader.py` | `overdue` · `cloud_gap` · `force_exit_due` · `FORCE_EXIT_EARLY_MIN` | `tests/test_exit_failsafe.py` (9) |
| 4-b | 손절 주문 체결 우선 | `core/autotrader.py` | `STOP_LIMIT_OFFSET_BPS=120` | `test_autotrader.py` |
| 5 | 신규 모델 + A/B | `core/regime_model.py`, `scripts/run_regime_ab.py` (신규) | `simulate` · `ab_compare` · `live_state` | `tests/test_regime_model.py` (6) |

구체 동작:

1. **`live_integrity`** — VIX를 ① 일봉 종가 ② 대체 소스 ③ 같은 세션 고가 중 **최댓값**으로 게이팅한다.
   실적일(11-19)이라면 27.14가 아니라 **28.99**로 판정해 신규 롱을 막는다. 스냅샷이 6분 넘게
   오래됐거나 VIX 봉 날짜가 세션과 다르면 VIX를 99로 올려 **모든 신규 진입을 차단**한다.
   청산 판정 가격은 5분봉·일봉·브로커 시세 중 **최저값**을 쓴다. 결과는 `data/live_integrity.json`.
2. **`risk_guard`** — 모든 자동 포지션에 `-8%` 또는 `-$25`(자산 2% 중 작은 쪽) 하드캡. 청산 규칙보다
   **먼저** 평가한다. NVDA 같은 사용자 잠금 포지션은 엔진이 레벨을 바꾸지 않고 5% 경고·15% 재난 캡만
   적용한다(지정 손절선이 어떤 이유로 미집행일 때의 마지막 방어선).
   실적 전 24시간·후 6시간은 신규 진입 금지, 보유 포지션은 `event_risk` 플래그로 보고.
3. **`exit_authority`** — 브래킷마다 소유자를 하나 정한다(`USER_LOCKED` / `SPEC:BEAR_ALPHA_HEDGE` /
   `DAILY_SWING` / `AVG_ANCHOR`). 평단 엔진은 SPEC·잠금 브래킷을 **덮어쓰지 않는다**.
   `avg_anchor_plan.json`의 해당 사다리는 `advisory_only: true` + `authoritative_levels`로 표시되고,
   충돌은 `data/exit_authority.json`에 기록된다.
4. **`exit_failsafe`** — 강제청산을 **05:30(10분 선행)** 에 발동하고, 놓친 건은 `force_exit_due`로
   브래킷에 남겨 **다음 정규장 첫 사이클에 시각과 무관하게 즉시 청산**한다. 정규장 밖 사이클도
   `FAILSAFE_OVERDUE`로 연체를 기록한다(종전 SKIPPED). 정규장 사이클이 45분 이상 비면
   `scripts/exit_watchdog.py`가 `gh workflow run`으로 사이클을 깨운다.
5. **손절 주문** — 손절 판정 시 지정가를 `-30bp`에서 **`-120bp`**로 밀어 체결을 우선한다
   (SOXL처럼 -3.5%p 미끄러지는 일을 줄인다).

## 4. 기존 vs 신규 모델 A/B (요약)

전체 표·민감도·연도별은 [`docs/regime_model_20261120.md`](regime_model_20261120.md),
원자료는 `data/regime_ab_20261120.json`. 5년 일봉(2021-11-17~2026-11-17), IS 70% / OOS 30%,
왕복 0.24% 차감.

| 모델 | 거래 | 기대값/건 | PF | MDD | 누적수익 | Sharpe | OOS 기대값 |
|---|---|---|---|---|---|---|---|
| 기존 DIP_SWING_DAILY | 153 | +1.21% | 1.28 | -19.53% | +42.66% | 0.46 | +3.43% |
| **신규 V2 (채택 후보)** | 91 | **+2.12%** | **1.66** | **-12.84%** | +43.24% | **0.72** | **+4.48%** |

게이트: 전체 표본 91건, OOS 38건, OOS ΔEV +1.05%p, OOS PF 2.86, MDD 6.69%p 개선 — **통과**.
실측으로 **기각한** 아이디어: CRISIS 전면 롱 금지(OOS EV 3.28%), SQQQ 헤지(3.76%·IS -0.09%),
패닉 필터(3.27%), VIX32 레짐 청산(발동 0건). 근거와 함께 기본값에서 뺐다.

## 5. 현재 상태·남은 결정

- **실주문 없음**: `config/live_execution_gate.json`은 2026-11-20 06:00 KST에 만료됐고 이번 작업에서
  건드리지 않았다. 클라우드 사이클은 DRY_RUN으로 돈다.
- **SQQQ 1주가 연체 상태**로 남아 있다(05:40 룰 미집행). 새 게이트가 켜지면 다음 정규장 첫 사이클이
  연체분을 먼저 청산한다. 게이트 없이는 청산되지 않는다 — 사용자 결정 사항.
- 신규 모델은 **그림자 판정**만 한다(`autotrader_status.shadow_model`). 실집행 전환은 별도 승인 필요
  (`data/strategy_buildup.json` H-0008, `auto_apply: false`).
- VIX 28 하드 차단은 브리프 요구대로 유지했다. 백테스트에서는 28 차단이 기대값을 2.12% → 1.72%로
  깎았다(팬데믹 이후 반등일을 놓치기 때문). 위험 지시가 우선이라 유지하되, 수치를 함께 남긴다.
