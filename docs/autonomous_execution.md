---
title: "무인 자동매매 & 10분 업무보고 시스템 (AUTONOMOUS_INTRADAY)"
updated: 2026-11-17 12:20 KST
---

# 무인 자동매매 & 10분 업무보고 시스템

동훈님 지시(2026-11-17 12:02 KST) 원문:
> "매수매도를 자동으로 장중에 판단해서 하더록 해. 매번 물어보지 말고. 대신 손실은 줄이고
> 이익은 극대화할수 있는 전략을 계속 업데이트하고 각 직원들이 10분 마다 각자 업무를
> 업데이트 해서 업무보고를 올릴 수 있도록해줘"

정본 설정: `config/swing_approval.json` → `autonomous_execution`
(`enabled: true`, `ask_before_trade: false`, `delegated_at_kst: 2026-11-17 12:02`).

## 1. 실행 명령 (스케줄 액션에 넣을 것)

```bash
# 장중 10분 사이클 — 무인 매수/매도 + 상태·브래킷 main 반영
cd us-capital-preservation && python3 scripts/autotrader.py --execute --push

# 10분 단위 10인 직원 업무보고 + 대시보드/피드 갱신
cd us-capital-preservation && python3 scripts/run_10m_pulse.py --push

# 판단만 확인(주문·상태 변경 없음)
python3 scripts/autotrader.py --dry-run
python3 scripts/autotrader.py --status
```

권장 구성: `autotrader`는 10분 간격, `run_10m_pulse`도 10분 간격(2분 정도 시차를 두면
푸시 충돌이 줄어든다). 기존 `🇺🇸-미국-정규장-장중-브래킷-매도-감시` 액션은
`--execute` 없이(읽기 전용) 두거나 일시정지한다 — 두 엔진이 같은 브래킷을 동시에
팔지 않도록 단일 작성자 리스(`data/autotrader_lease.json`)와 대기주문 차단이 있지만,
한 엔진만 집행하는 편이 명확하다.

## 2. 집행 경로 (중요)

`.github/workflows/nh_trade_dispatch.yml` 실제 스펙을 확인한 결과:

| mode | 동작 | 입력 |
|---|---|---|
| `test_balance` | `NHBroker.get_balance()` → 잔고·보유수량 JSON | — |
| `buy` | `place_order(ticker,'BUY',qty,price)` 지정가 | ticker, qty, price(>0) |
| `sell` | `place_order(ticker,'SELL',qty,price)` 지정가 | ticker, qty, price(>0) |
| `monitor` | 러너에서 `scripts/monitor_and_exit.py` 실행 | — |

- **`mode=place_order`는 존재하지 않는다.** 무인 엔진은 `mode=buy|sell`로 디스패치한다.
- 워크플로우는 시장가를 지원하지 않는다(`ahi_nmn_pr_tp_cd="00"` 고정, price ≤ 0 거부).
  체결 확률을 위해 **매수 +0.20%, 매도 −0.30%** 버퍼를 둔 지정가를 낸다.
- 로컬 `api.nhplug.com:8443`은 막혀 있어(Errno 111) 주문은 GitHub Actions에서 실행된다.
  로컬 API가 열려 있으면 `execution/order_bridge.py`가 직접 발주로 자동 폴백한다.
- 접수 확인: `gh run view <id> --log`의 `rsp_cd`/`Output_0.orr_no` 파싱
  (`00171`=주문완료). 체결 확인: 다음 사이클에서 `mode=test_balance`를 디스패치해
  `positions[qty·avg_price·sellable_qty]`와 체결내역(`cns_qty`/`cns_pr`)을 대조.

## 3. 하드 가드레일 (코드 강제, 정책 파일이 넘어설 수 없음)

| 항목 | 값 | 의미 |
|---|---|---|
| 1회 진입 예산 | 현금 20% / 자산 12% / 절대 $120 / 현금보존 $150 중 최소 | 현재 $426.63 현금 → **$85.33**, 변동성 계수 0.7 적용 시 **$59.73** |
| 거래당 리스크 | 자산의 1.5% ($13.4) | 손절폭 × 수량으로 계산해 초과 시 수량 축소 |
| 최대 보유 종목 | 4 | SGOV 캐리 제외 |
| 세션 신규 진입 | 3건 / 세션 주문 8건 | |
| 일일 실현손실 한도 | -$25 와 -3% 중 작은 쪽 | 도달 시 **당일 신규 매수 중단**(청산은 계속) |
| 개장 유예 | 23:30~00:00 신규 매수 금지 | 손절 유예는 23:45 |
| 신규 진입 마감 | 04:30 | 이후 청산만 |
| 강제청산 | 05:40 | 3배 레버리지로 NVDA 실적(11-18 06:20)을 넘기지 않음 |
| VIX 계수 | ≥20 → 0.75, ≥24 → 0.5, ≥28 → 0 | 신규 진입 축소·중단 (20~24 구간은 2026-11-17 13F A/B 채택) |
| 중복 방지 | 멱등키(세션·종목·방향·수량·가격) 10분 창 + 종목 쿨다운 20분 | 같은 주문 2회 전송 차단 |
| 물타기 | 금지 | 보유 종목 추가 매수 차단(코드) |
| NVDA | TP $173.0 / SL $150.0 잠금 | 정책 파일로도 변경 불가 |
| 금지 종목 | ENVX·IBRX·SOUN | 11-13 지시 유지 |
| 비상 정지 | `data/autotrader_halt.json` 존재 시 신규 매수 전면 중단 | 청산은 계속 |

## 4. 진입 신호 (사용자가 이미 승인한 것만)

| 신호 | 조건 | 청산 |
|---|---|---|
| `DIP_SWING_DAILY` | RSI2 ≤ 20 · %B ≤ 0.15 · RVOL ≥ 1.0 (완료 세션) → 세션 초반 90분 진입 | v2 샹들리에 |
| `DIP_SWING_INTRADAY` | 5분봉 동일 임계값 (00:00 KST 이후) | v2 샹들리에 |
| `BEAR_ALPHA_HEDGE` | VIX ≥ 24.0 + 확인 2개(DXY ≥ 104.5 / 센티 < 40) → SOXS·SQQQ | v2 샹들리에, 명목 상한 $60 |
| `NVDA_EARNINGS_GAP_SOXS` | 2026-11-18 23:30 NVDA 갭 ≥ +2% → SOXS 1주 | 05:55 종가 청산, 재난손절 -8% |

## 5. 청산 규칙 (단일 구현: `core/exit_rules.py`)

- **사다리**: 실적 가드(05:40) → 손절(23:45 유예 후) → TP1(1주, 손절 본전 상향) → TP2.
- **v2 샹들리에**: 초기손절 0.75×ATR(1.5~8%), 트레일 0.9×ATR, 1ATR 전진 후 본전 하한, 3세션 시간청산.
- **DIP_SWING**: ATR×1.5 대칭, 전량 청산, 3세션 상한.
- 근거: 2년 60분봉 A/B — SOXL v1 −1.31%/건 → v2 +2.48%/건, TQQQ −0.33% → +0.70%/건.

## 6. 전략 자기 업데이트 (`core/strategy_evolver.py`)

| 업데이트 | 발동 조건 | 효과 |
|---|---|---|
| 변동성 타겟 사이징 | 매 사이클 | `risk_scale = clamp(10 / 평균ATR, 0.7, 1.0)` — 명목 예산만 축소, 주문 레벨 불변 |
| 방어 모드 | 최근 20건 기대값 < −0.5%/건 & PF < 0.8 | 진입 필터 강화(RVOL 1.5, %B 0.10), 손절 1.0×ATR, 트레일 0.7×ATR |
| 파라미터 교체 | 표본 ≥ 20건 **그리고** ΔEV ≥ +0.10%p · PF ≥ 1.2 · MDD 악화 ≤ 2%p · 클램프 내 | 트레일·손절 배수 변경 |

현재 실전 표본 **7건**(11-11~11-13 실현, 승률 71.4%)이므로 파라미터 교체는 **보류** 상태이며,
로그(`data/strategy_evolution_log.json`)에 `NO_CHANGE — 표본 7건 < 최소 20건`으로 남는다.

## 7. 10분 업무보고 파이프라인

`scripts/run_10m_pulse.py` 한 번에:

1. 실전 원장 동기화 (0.7s)
2. 10인 에이전트 파이프라인 + 피드 (9s 이내, 백테스트는 24h 캐시 재사용)
3. 직원 10명이 각자 **담당 업무 + 현재 실측값 + 다음 행동**을 1건씩 게시
   (`post-pulse-XX-...`, 10건) + 무인 집행 현황 고정 게시물 1건
4. `cube.html`·`feed.html` 재렌더(1s) + 위젯 번들 동기화
5. `--push`면 커밋·푸시

실측 소요: **총 11.2초**(원장 0.7 + 에이전트 9.3 + 렌더 1.0). 목표 10~20초 이내.

## 8. 파일 지도

| 파일 | 역할 |
|---|---|
| `core/execution_policy.py` | 정책 로드·가드레일·예산·손실한도·VIX 계수 |
| `core/autotrader.py` | 사이클(조정→청산→진입→전략점검) |
| `core/exit_rules.py` | 청산 판정 단일 구현 |
| `core/entry_signals.py` | DIP(일봉·5분봉)·BEAR ALPHA·NVDA 갭 신호 |
| `core/strategy_evolver.py` | 전략 자기점검·파라미터 게이트 |
| `core/session_time.py` | KST 세션 시각 비교 |
| `execution/order_bridge.py` | GitHub Actions 주문·잔고 조회·로그 파싱 |
| `scripts/autotrader.py` | CLI(`--execute/--dry-run/--status/--push`) |
| `scripts/run_10m_pulse.py` | 10분 업무보고 파이프라인 |
| `scripts/monitor_and_exit.py` | 감시·리포트(청산 판정 공유, `--execute`는 브리지 경유) |
| `tests/` | 54개 단위·통합 테스트 |

데이터: `data/active_brackets.json`(브래킷 정본), `data/autotrader_state.json`(세션 상태·대기주문),
`data/autotrader_status.json`(현황), `data/autotrader_log.jsonl`(사이클 로그),
`data/live_order_responses.jsonl`(주문·응답), `data/strategy_trade_ledger.json`(실전 표본),
`data/strategy_params.json`(현행 파라미터), `data/strategy_evolution_log.json`(점검 이력).

## 9. 검증 결과 (2026-11-17 12:0x KST)

- 단위·통합 테스트 **54개 전부 통과**(0.8~3.4초): 가드레일 클램프, 예산·리스크 사이징,
  손실 서킷브레이커, 개장 유예·04:30 마감, 물타기 차단, 중복 차단, TP1/TP2/SL/강제청산/
  샹들리에/시간청산, 브리지 로그 파싱, 매수→체결→손절 엔드투엔드.
- 실계좌 읽기 검증: `mode=test_balance` 디스패치 → 파싱 성공
  (equity $895.78, cash $636.01, SOXL 2 @50.43 · TQQQ 2 @54.00 · NVDA 1 @173.30 · SGOV 1 @100.53, 전량 매도가능).
- `python3 scripts/run_full_pipeline.py`: OK (13.5초, agents 10/10).
- `python3 scripts/run_10m_pulse.py`: OK (11.2초, posts 30).

## 10. 남은 한계

- 주문 체결가·수량은 **다음 사이클**의 잔고 조회로 확정된다(디스패치 응답은 접수까지만 알려준다).
  미체결로 남으면 `STALE_UNFILLED`로 표시하고 같은 종목 추가 주문을 막는다(취소 기능은 워크플로우에 없음).
- 워크플로우 파일은 `workflows` 권한 제약으로 수정하지 않았다. 시장가·취소·정정이 필요하면
  동훈님이 워크플로우에 모드를 추가해야 한다.
- 실전 표본 7건으로는 손절폭·트레일 최적화를 검증할 수 없다. 20건이 쌓이기 전까지는
  방어 모드·변동성 계수만 작동한다.
- 개장 유예(23:30~00:00) 때문에 일봉 DIP_SWING 신호의 진입은 00:00 KST에 실행된다.
  검증 백테스트의 '다음 세션 시가 진입'보다 30분 늦은 진입이며, 신중 모드가 끝나는
  11-18 06:20 이후에도 이 유예는 유지된다(갭 휩쏘 방지 목적, `guardrails.opening_blackout_kst`).
- `data/autotrader_status.json`의 `mode`는 마지막 실행 모드다. 스케줄 액션이 `--dry-run`으로
  돌면 `DRY_RUN`으로 표시되므로, 운영 중에는 `--execute`만 사용한다.
