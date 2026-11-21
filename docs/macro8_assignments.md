# CUBE-M8 — 10인 세부업무 배정 (2026-11-21)

동훈님 지시(2026-11-21 11:01)에 따라 8대 거시·자산 선행지표 프로젝트의 세부 업무를
CUBE 10인에게 나눴다. 각 항목은 `data/agent_collab_bus.json`에 동일 내용으로 기록되어
있고, `correlation_id = LEI8-20261121` 스레드가 설계→검증→판정→집행 흐름을 잇는다.

| # | 담당 | 세부 업무 | 산출물·근거 |
|---|---|---|---|
| 1 | 한지우 (뉴스·센티먼트) | CPI·PPI·고용 발표일 뉴스 톤과 M8 블록 z 변화의 교차검증. 발표 주간 헤드라인 리스크 플래그를 센티먼트 앵커에 반영 | `data/news_sentiment_report.json`, `data/macro_leading.json` |
| 2 | 이도현 (멀티에셋 기술적) | 자산군별 모멘텀(60일)·실현변동성(20일)·200일선 괴리 피처의 z 창 민감도(756일 vs 504일) 검증, ATR 대비 스케일 점검 | `core/macro_leading.py` MEMBER_SPECS |
| 3 | 박서연 (실적·매크로 일정) | 8대 지표 **발표 캘린더·발표시차 DB** 유지: CPI 13일·PPI 15~16일·고용 7일·청구 6일 가정을 실제 발표일과 대조하고 D-day 알림 | `core/macro_leading.py` FRED_META `lag_days` |
| 4 | 최민준 (매크로·채권) | 금리·기대인플레 블록 설계와 부호(사전부호 vs IS 재적합) 검증. 실질금리·손익분기·5y5y 포워드 해석 | `data/macro_leading_backtest.json` `block_ic` |
| 5 | 정하린 (스마트머니) | 크립토·금·유가 블록을 기관 수급(BTC ETF·금 ETF·원유 포지셔닝)으로 교차검증, 블록 z와 실제 플로우 괴리 감시 | `data/superinvestor_13f.json`, M8 `blocks` |
| 6 | 강우진 (퀀트 시나리오) | M8 z → 레짐·노출 계수 매핑 설계(선형/단계/이진)와 시나리오 확률·종목 상한 반영(그림자) | `core/macro_leading.py` `linear_scale`·`regime_for` |
| 7 | 윤태호 (백테스트) | A/B·IS/OOS·+10일 시차·BTC 블록 제외 강건성 재현 검증. `_run`의 신호→다음날 적용 규약 감사 | `scripts/run_macro_lei_backtest.py` |
| 8 | 임수아 (알파·리스크) | IC·Newey-West t값·분위 분석으로 채택/보류 판정. 부호 안정성(47.6%)이 낮은 점을 근거로 `advisory_partial` 판정 | `data/macro_leading_backtest.json` `verdict` |
| 9 | 송재원 (총괄) | 매매 판단기준 연동 정책: shadow → advisory → active 3단계, `enabled_for_sizing`은 동훈님 승인 게이트로 유지 | `config/macro_leading_policy.json` |
| 10 | 김시우 (집행·운영) | 자동 수집 파이프라인 운영: 4시간 주기 워크플로·pulse 연동(3시간 stale 시 갱신)·발행 검증·대시보드 반영 | `.github/workflows/macro_leading_sync.yml`, `scripts/sync_macro_leading.py` |

## 검증 스레드(요약)

1. **가설(최민준, #4)** — 8대 지표의 표준화 복합 z는 SPY·QQQ·SOXX의 향후 20거래일 수익률을 선행한다.
2. **검증 요청(→ 윤태호, #7)** — 발표시차 반영·OOS 분리·SMA200 기준선 비교.
3. **검증 결과(#7)** — OOS IC 0.291 (Newey-West t 2.57), SMA200 대비 Sharpe 개선 3/3, 단순보유 대비 SPY Sharpe +0.40. 단 부호 안정성 47.6%.
4. **판정(#8)** — `advisory_partial`: MDD 억제 효과는 실재하나 규칙 강제에는 부족 → 그림자 유지.
5. **총괄 보류(#9)** — 실전 사이징 반영은 동훈님 승인 대기(`enabled_for_sizing=false`).
6. **운영(#10)** — 수집·발행 파이프라인 가동, 주문 경로와 무관.
