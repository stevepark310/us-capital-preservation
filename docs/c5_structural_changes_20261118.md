# C5 파일럿 + 구조 개선 3건 실전 반영 보고 (2026-11-18)

승인: 박동훈(사용자) `2026-11-18 11:37 KST` — "C5 파일럿(개장30분 방향 1일1회) + 구조 개선 3건 적용"
추가 지시: `2026-11-18 12:05 KST` — "DIP_INTRADAY는 0.9 ATR 유지, DIP_SWING_DAILY만 1.5~2.0 ATR로 분리"
근거 문서: `docs/intraday_inflection_research_20261118.md`, `data/research_10m_5d_20261118.json`,
`data/research_10m_ab_20261118.json`

> 모든 수치는 실주문 없이 10분봉 재현 시뮬레이션(왕복 0.24%, 체결=판정 봉 종가)으로 측정했다.
> 실전 계좌 손익과 섞지 않는다.

---

## 0. 결론

| 항목 | 결과 |
|---|---|
| C5 파일럿 | **적용**. 60일 단일 레그 +$14.64(58건, 승률 46.6%) · 5일 −$7.81(5건) |
| ① 숏 게이트 1-of-1 | **적용**. 60일 발동 사이클 0 → 29회, 숏 체결 3건(손익 ≈ $0). VIX 하한 24.0은 코드로 고정 |
| ② RVOL 가점 전환 | **원복**(2026-11-18 14:25). 60일 기대값 −$10.9 · 5일 +$2.34였고, 사용자 판단("기대값을 낮춘다면 리스크는 같은데 수익만 적다")으로 **RVOL ≥ 1.0 필수 복귀**. 아래 §4-5, §7 참조 |
| ③ 브래킷 규격 분리 | **적용**. 장중 트레일 **0.9×ATR 유지**(일괄 1.5는 5일 −$2.72→−$7.75, 60일 −$31.29→−$44.80로 악화). 넓히는 것은 일봉 스윙(ATR×1.5 대칭)에만 |
| 불변 가드레일 | 6개 전부 유지(현금 $150 · 일손실 $25 · NVDA 173/150 · VIX≥28 금지 · ask_before_trade false · 1회 예산 $150) |
| 테스트 | `pytest` **113 passed** (C5 15 · 구조 25) |
| dry-run | `scripts/autotrader.py --dry-run` 정상(2026-11-18 14:31 KST 재확인, OFF_HOURS, 주문 0) |

**전체 Before → After(적용본)**: 5거래일 −$3.00 → **−$11.74**, 60거래일 −$27.21 → **−$15.56**.
60일은 개선(+$11.65), 5일은 C5 5건(−$7.81) 때문에 악화. 5일 표본은 연구 문서에서도 C5가 중립(+$0.10, 2레그)이었던 창이다.

---

## 1. C5 파일럿 — 개장 30분 방향 1일 1회

| 항목 | 값 | 근거 |
|---|---|---|
| 진입 시각 | 10:00 ET = **00:00 KST**, 창 30~60분(00:00~00:30) | 개장 유예 직후 1회 |
| 방향 | 10:00 가격 vs **전일 종가** → 위면 LONG(SOXL·TQQQ), 아래면 SHORT(SOXS·SQQQ) | 연구 C5와 동일 규칙(같으면 SHORT) |
| 청산 | **15:40 ET = 05:40 KST** 종가 청산 | `force_exit_kst` |
| 손절 | **3.0 × 5분봉 ATR** (2~15% 클램프) | 손절 2~5×ATR 전 구간 플러스(민감도 §4.4) |
| 예산 | **$150** (파일럿 상한, `notional_cap`) | 승인 조건 |
| 식별자 | `entry_source = C5_OPEN30_DIRECTION`, bracket_class `PILOT_C5` | 원장·피드 분리 기록 |
| 1일 1회 | `st.entries`·`pending`·열린 브래킷 3중 확인 | 중복 진입 차단 |
| 충돌 방지 | C5 보유 중 같은 세션 `BEAR_ALPHA_HEDGE`·반대방향 DIP 진입 차단, 티커가 물려 있으면 대체 티커(SOXL↔TQQQ / SOXS↔SQQQ) | 롱·숏 동시 보유 금지 |
| 진입 제외일 | ① 명시 블랙아웃 ② **실적 반응일 자동 계산** | 오늘 11-18 = NVDA 반응일 |

### 1.1 오늘(2026-11-18 ET 세션) 동작 예정
```
23:30 KST 개장      → 신규 매수 없음(개장 유예 23:30~00:00) · NVDA 시가 확인
00:00 KST           → C5 파일럿 [진입 제외] — "명시 블랙아웃 2026-11-18 · 실적 반응일(NVDA)"
                      → 별도 승인된 NVDA_EARNINGS_GAP_SOXS(갭 ≥ +2% → SOXS 1주, 05:55 청산)만 대기
00:00 이후          → DIP_SWING(일봉·장중) · BEAR_ALPHA(1-of-1) 정상 가동
05:40 KST           → 장중·C5 브래킷 세션 종가 청산
```
실적 반응일 계산: `data/earnings_calendar.json`의 `report_at_kst`를 ET로 환산해 **마감 후 발표면 다음 거래일,
개장 전 발표면 같은 날**을 반응일로 잡는다(NVDA 11-18 06:00 KST = 11-17 16:00 ET → 반응일 11-18).
11-12 NVDA −14% 갭 후 장중 +11.2% 반전에서 C5가 −$12.76를 잃은 구조를 같은 날 겹쳐 맞지 않게 막는 장치다.

---

## 2. 구조 개선 3건

### ① 숏 게이트: 2-of-2 → 1-of-1 (VIX 하한 24.0 고정)
- 확인 후보: DXY ≥ 104.5 / 평균 센티먼트 < 40 / **가격구조(SOXL < 당일 세션 VWAP, 5분봉 계산)**.
- `BEAR_GATE.vix_floor = 24.0`을 코드 상수로 두어 config로도 24 미만은 열 수 없다(위임 밖).
- 검증: 60일 재현에서 게이트 발동 사이클 **0 → 29회**, 실제 숏 체결 3건(SQXS/SQQQ) 손익 ≈ $0.
  11-11~17 구간은 VIX 22~27로 24를 넘는 날이 1일뿐이라, 완화 후에도 이 주간 숏은 여전히 드물다(연구 진단 그대로).
- `HEDGE_NOTIONAL_CAP $60`은 유지 → SOXS·SQQQ 각 1주. 슬롯 상한($60)이 예산 하한($150)보다 작아도
  주문이 막히지 않도록 하한 계산은 `min(하한, raw, 슬롯)` (11-18 10:30 수정 유지). C5 인버스는 별도 $150 슬롯.

### ② RVOL — 11:37 가점 전환 → **14:25 필수로 원복(현행)**
- 현행: 필수 조건은 **RSI2 ≤ 20 · %B ≤ 0.15 · RVOL ≥ 1.0** 세 가지. 셋 다 통과해야 신호다.
- RVOL이 1.0을 넘는 신호는 점수 가점(최대 +20점)을 받아 같은 세션 3건 한도 안에서 우선순위를 정한다.
- 승인 조건은 **하한**으로 집행된다: `core/entry_signals.resolve_entry_filters`가
  `config/swing_approval.json entry_conditions`와 `data/strategy_params.json entry_filters`를 합칠 때
  더 엄격한 쪽을 쓴다(RVOL 최소값은 큰 쪽, RSI2·%B 상한은 작은 쪽, `rvol_required`는 OR).
  전략 파일이 느슨해져도 승인 조건을 풀 수 없고, 방어 모드(RVOL ≥ 1.5·%B ≤ 0.10)처럼 조이는 것은 그대로 먹는다.
- 일봉 채널(`swing_engine.signal_row`)도 같은 규칙(`rvol_required=True`).
- 11:37~14:15 사이 적용본의 측정: 5일 +$2.34, **60일 −$10.9**(추가된 RVOL<1 진입의 건당 기대값 −$0.67).

### ③ 브래킷 규격 분리 (`core/bracket_specs.py`)
| 신호 | class | ATR 기준 | 손절 | 익절/청산 |
|---|---|---|---|---|
| DIP_SWING_DAILY | DAILY_SWING | **일봉** ATR | 1.5×ATR (3~15%) | **+1.5×ATR (3~20%) · 3세션** |
| DIP_SWING_INTRADAY | INTRADAY | 5분봉 ATR | 0.75×ATR (1.5~15%) | 트레일 **0.9×ATR** · 05:40 세션청산 |
| BEAR_ALPHA_HEDGE | INTRADAY | 5분봉 ATR | 0.75×ATR | 트레일 0.9×ATR · 05:40 세션청산 |
| C5_OPEN30_DIRECTION | PILOT_C5 | 5분봉 ATR | 3.0×ATR (2~15%) | 05:40 시간청산(트레일 없음) |
| NVDA_EARNINGS_GAP_SOXS | HEDGE_EVENT | — | 재난손절 8% | 05:55 시간청산 |

- 일봉 채널은 chandelier를 빌려 쓰지 않고 **승인된 DIP_SWING 규격(ATR×1.5 대칭·3세션, `swing_hold`)** 으로 돌린다.
  기존에는 일봉 ATR(20~26%)이 0.9×ATR 트레일 ≈ 23%로 들어가 "3세션 방치" 브래킷이 됐다.
- 장중 채널은 **0.9×ATR 유지**(12:05 지시). 정본은 `data/strategy_params.json trail_atr_mult`(클램프 0.60~1.20)이며
  `strategy_evolver`가 조정할 여지는 그대로 남긴다.
- 장중 브래킷에 `force_exit_kst 05:40`을 붙여 세션을 넘기지 않게 했다(신중 모드 종료 후에도 유지).
- 기존 보유 브래킷은 자기 필드를 그대로 쓰므로 소급 변경 없음.

---

## 3. 변경 파일

| 파일 | 변경 |
|---|---|
| `core/bracket_specs.py` | **신규** — 진입 채널별 브래킷 규격 표(일봉/장중/C5/이벤트) |
| `core/entry_signals.py` | C5 신호·블랙아웃·방향 판정, 숏 게이트 1-of-1 + VWAP 가격구조, `dip_signal_from_metrics`(RVOL 필수), `resolve_entry_filters`(승인 조건=하한), 5분봉 ATR 헬퍼 |
| `core/autotrader.py` | `_c5_candidate`·`_c5_done_today`·`_c5_conflict`·`_c5_status`, `_new_bracket` 규격 라우팅, 후보 우선순위에 C5, 상태에 `c5_pilot`·`bracket_class` |
| `core/exit_rules.py` | chandelier_v2에 세션 종가 청산(`force_exit_kst`) 추가, 문서·주석 갱신 |
| `core/swing_engine.py` | `rvol_required` 지원(현행 True = RVOL 필수, 14:25 원복) |
| `core/strategy_evolver.py` | `rvol_required` 기본 True, 방어 모드에서 RVOL 1.5·%B 0.10으로 강화, 트레일 클램프 0.60~1.20 유지 |
| `core/execution_policy.py` | 위임 경계 문서 갱신(11-18 승인 반영), `c5_pilot`·`bracket_specs`·`bear_alpha_gate` 정책 노출 |
| `config/swing_approval.json` | `c5_pilot`·`bear_alpha_gate`·`bracket_specs` 블록, `entry_sources`에 C5, `entry_conditions.rvol_required=true`(14:25 원복), `preserved_controls.guardrails_20261118` |
| `data/strategy_params.json` | `rvol_required=true`, 트레일 0.9 유지 사유 기록 |
| `tests/test_c5_pilot.py` | 신규 15건 |
| `tests/test_structural_improvements.py` | 신규 19건 |
| `scripts/research/run_after_ab.py` | 신규 — Before/After A/B 하네스(프로덕션 함수 직접 호출, feature-detect로 구버전 코드도 재현) |
| `data/ab_20261118/*.json` | A/B 원자료 |

---

## 4. Before / After 실측

### 4.1 방법
- `scripts/research/run_after_ab.py`가 **프로덕션 함수를 직접 호출**한다(복사본 아님):
  `entry_signals.dip_daily` · `dip_signal_from_metrics` · `bear_alpha_gate` · `c5_open30_signal` ·
  `AutonomousTrader._size` · `_new_bracket` · `_sl_pct` · `exit_rules.decide` · `execution_policy`.
- **Before = 개선 전 커밋(2ac6b09) 체크아웃**에 같은 스크립트를 넣어 실행(feature-detect로 C5·RVOL 가점·가격구조 확인·규격표가
  없는 상태 = 그 시점 규칙). 5거래일 **10건 −$3.00 · 승률 30% · PF 0.61 · MDD −$8.20 · 수수료 $2.89**로
  연구 문서(−$2.98 · 30% · 0.61 · −$8.18 · $2.89)를 재현 → 하네스 신뢰 근거.
- 60일 창: 2026-08-25~11-17, 10분봉 39봉/일, SOXL·TQQQ·SOXS·SQQQ·^VIX·DX-Y.NYB.

### 4.2 전체 결과 (예산 $150, 왕복 0.24%)

| 실행 | 5일 건수 | 5일 손익 | 5일 PF | 60일 건수 | 60일 손익 | 60일 PF | 60일 MDD | 60일 수수료 |
|---|---|---|---|---|---|---|---|---|
| **Before** (개선 전) | 10 | −$3.00 | 0.61 | 83 | −$27.21 | 0.68 | −$48.16 | $23.58 |
| ①+③만 (RVOL 필수, C5 없음) | 11 | −$2.72 | 0.67 | 86 | −$31.29 | 0.65 | −$52.52 | $23.91 |
| ①+②+③ (C5 없음) | 14 | −$0.38 | 0.98 | 133 | −$41.90 | 0.66 | −$48.64 | $37.73 |
| ①+②+③ + C5 (RVOL 필수) | 9 | −$11.74 | 0.46 | 91 | −$4.65 | 0.95 | −$38.88 | $26.22 |
| **적용본** (C5 + RVOL 가점) | 9 | −$11.74 | 0.46 | 116 | **−$15.56** | 0.88 | −$47.50 | $34.12 |

### 4.3 항목별 기여 (60일)
| 축 | 비교 | 효과 |
|---|---|---|
| C5 파일럿 | (①+②+③)=−$41.90 → 적용본 −$15.56 | **+$26.3** |
| ② RVOL 가점 | RVOL 필수 −$4.65 → 가점 −$15.56 | **−$10.9** (5일은 +$2.34) |
| ③ 브래킷 분리 | Before −$27.21 → ①+③ −$31.29 | −$4.1 (일봉 브래킷 교체분, 20건 표본) |
| ① 숏 게이트 | 60일 숏 체결 3건, 손익 ≈ $0, 게이트 발동 0 → 29사이클 | 손익 중립(발동 가능성 확보) |

### 4.4 트레일 민감도 (적용본 기준, 60일)
| 장중 트레일 | 5일 | 60일 |
|---|---|---|
| 0.9×ATR (적용) | −$11.74 | −$15.56 |
| 1.5×ATR | −$11.88 | −$10.51 |
| 2.0×ATR | −$11.96 | −$5.53 |
→ 5일에서는 0.9/1.5/2.0 차이가 없고(모두 −$11.7~−$12.0, C5가 지배), C5·RVOL을 고정한 구조 비교에서는
0.9가 1.5보다 5일 −$2.72 vs −$7.75, 60일 −$31.29 vs −$44.80로 우수 → **0.9 유지**가 12:05 지시와 일치.
2.0은 60일에서만 좋고 5일에서 나쁘다(표본 의존). 표본 20건 이상 축적 후 `strategy_evolver`가 0.6~1.2 범위에서 판단한다.

### 4.5 해석과 한계
1. **C5는 60일에서만 플러스**(+$14.64/58건, 건당 +$0.25). 5일 5건 −$7.81로 표본 의존이 크다 — t=0.71(연구) 그대로.
   단일 레그라 연구의 2레그 합(+$40.09)보다 작다. 2주 파일럿 후 판정이 맞다.
2. **RVOL 가점은 60일 표본에서 기대값을 떨어뜨렸다**(추가된 RVOL<1 진입의 건당 기대값 약 −$0.67).
   연구의 11-12 사례(단일일)는 60일로 일반화되지 않았다. 사용자가 2026-11-18 14:25 이 항목만 원복을 지시해
   `entry_conditions.rvol_required = true`로 돌렸다(§7).
3. 일봉 채널 교체는 이 창에서 손익 중립(모두 3세션 시간청산, 표본 20건) — 구조 정합성 목적으로 유지.
4. C5는 하루 1건을 세션 3건 한도에 포함시키고 보유 종목을 점유하므로 DIP 건수를 줄인다(60일 DIP 99 → 54).
   이 크라우드아웃이 5일 손익을 더 나쁘게 만든 주된 경로다.

---

## 5. 불변 가드레일 확인 (dry-run 2026-11-18 11:49 KST)

```
계좌 평가 $883.5 / 현금 $636.01 · 당일 실현 $-8.43 (한도 -$25.0) · halted=False
1회 진입 예산 $150.0 (binding=abs_cap, VIX 25.46 → 계수 0.5 × 변동성 0.7 = 0.35)
보유 NVDA 1주 @ $173.3 · SL $150.0 · fill_anchored_v2
전략점검 NO_CHANGE: 표본 9건 < 최소 20건 — 파라미터 교체 보류
다음 게이트: 다음 정규장 2026-11-18 23:30 KST
```
- `cash_reserve_floor_usd 150.0` · `daily_realized_loss_limit_usd 25.0` · `entry_budget_cap 220` ·
  `NVDA TP 173.0 / SL 150.0` · `VIX ≥ 28 신규 진입 금지` · `ask_before_trade false` · `세션 3건` — 전부 그대로.
- 상태 JSON에 `c5_pilot`(블랙아웃·보유·당일집행·창·청산시각)과 포지션별 `bracket_class`가 추가돼 대시보드에서 확인 가능.

## 6. 되돌리기 스위치
| 항목 | 방법 |
|---|---|
| C5 파일럿 중단 | `config/swing_approval.json → autonomous_execution.c5_pilot.enabled = false` |
| RVOL 필수 | **원복 완료**(2026-11-18 14:25, `entry_conditions.rvol_required = true`). 다시 가점으로 돌리려면 `false` — 60일 −$10.9라 권장하지 않음 |
| 숏 게이트 2-of-2 복귀 | `autonomous_execution.bear_alpha_gate.confirmations_required = 2` |
| 브래킷 규격 | `autonomous_execution.bracket_specs` 값 수정(코드 배포 불필요, 다음 사이클 반영) |

---

## 7. 2026-11-18 14:25 — RVOL 필수 원복 기록

사용자 지시(14:25): "기대값을 낮췄다는건 그만큼 리스크는 있고 수익은 적다는거지? 그러면 이걸 적용하지 않는게 좋을듯해."
→ 구조 개선 ②만 취소하고 ①(숏 게이트 1-of-1)·③(브래킷 분리·장중 0.9 ATR)·C5 파일럿은 그대로 둔다.

| 변경 | 값 |
|---|---|
| `config/swing_approval.json entry_conditions` | `rvol_required: false → true`, `rvol_history`(11:37 가점 → 14:25 원복, 지시 원문) 기록 |
| `data/strategy_params.json entry_filters` | `rvol_required: false → true` (집행 파라미터) |
| `core/entry_signals.py` | `DIP_THRESHOLDS.rvol_required=True`, `resolve_entry_filters()` 신설(승인 조건=하한) |
| `core/swing_engine.py` | `DIP_PARAMS.rvol_required=True`, 차단 사유 문구 "RVOL x<1.0(필수)" |
| `core/strategy_evolver.py` | `BASE_PARAMS.entry_filters.rvol_required=True`, DEFENSIVE_OFF 문구 |
| `core/autotrader.py` | `_entry_filters()`로 승인 조건을 합쳐 신호 계산에 전달(`_approval_entry_conditions`) |
| `tests/test_structural_improvements.py` | ② 테스트 재작성(RVOL 차단·하한 병합·집행 경로 6건 추가) |

검증(실행 결과 그대로):
```
effective filters: {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0, "rvol_required": true, "rvol_bonus_max": 20.0}
RVOL 0.66 → 신호 없음 "RVOL 0.66<1.0(필수)" · RVOL 1.42 → 신호(점수 58.7)
숏 게이트 1-of-1(VIX 하한 24.0) · C5 3.0×ATR / 예산 $150 그대로
체감 영향(최근 60세션 일봉 DIP): 과매도 19회 중 RVOL<1.0로 차단 2회(TQQQ 09-15 0.81, 10-23 0.93) — 실제로 걸러지는 건 저확신 2건
pytest 113 passed · autotrader --dry-run 정상(OFF_HOURS, 진입 0건 · 주문 2건은 기존 상태 표시)
```
주의: 승인 조건을 집행 경로가 실제로 읽게 만든 것이 이번 변경의 핵심이다. 이전에는 `config`만 true로 바꿔도
`data/strategy_params.json`의 false가 이겨서 RVOL 게이트가 살아나지 않았다.
