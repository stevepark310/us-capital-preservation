# ⚡ 판단→실전 주문 연결과 프리장·본장 즉시 개입 체계 (2026-11-21)

- 작성: 2026-11-21 (KST), 지시 원문: "실전 주문에도 이러한 판단의 결과. 업무의 결과들이 적용되어야해. 필요시 프리장이나 본장에 개입해서 바로바로 매매할 수 있게 해줘"
- 백테스트 산출물: `docs/m8_regime_combine_20261121.json`, `docs/premarket_intervention_20261121.json`
- 재현: `python3 scripts/research/m8_regime_combine.py` · `python3 scripts/research/premarket_intervention.py`

## 결론 (먼저)

1. **판단 근거가 주문까지 따라가게 됐다.** 무인 엔진이 주문 1건을 낼 때 CUBE-M8 국면·노출계수, V2 레짐, 10인 빌드업이 바꾼 진입 필터, 하드락 상태, VIX·예산 계산을 `decision_attribution`으로 함께 남긴다(주문 컨텍스트·상태 파일). shadow(참고)와 실제 반영(`used_in_sizing`)을 구분해 적는다.
2. **프리장 개입 경로는 코드·방어장치까지 완성됐고, 스위치는 닫혀 있다.** 네 겹 잠금을 정비해 `config/premarket_policy.json`의 스위치 + **사용자 승인 기록**만 있으면 `PRE_MARKET`에서 호가유형 `61`로 판단·주문이 실행된다. 다만 2026-11-21 재검증에서 **OOS t ≥ 2.0을 만족하는 조건이 없어** 자동 개입은 켜지 않았다(정책 파일의 `enable_requirements` 그대로).
3. **즉시 개입 단일 경로를 만들었다.** `scripts/intervene_trade.py`(기본 DRY_RUN) + `cloud_cycle.py intervene` + GitHub Actions `intervene_trade.yml`(수동 실행). 사람이 지금 사고팔 때 10분 사이클을 기다리지 않는다.
4. **월요일(11/23) 실사용 조건**: 현재 `live_execution_gate.json`은 **매도 전용**(SQQQ 1주 개장 익절)이다. 따라서 월요일 즉시 개입은 **매도만** 가능하고, 매수 개입·프리장 매수는 새 승인(게이트 한도·프리장 승인)이 필요하다. 아래 "승인 항목" 참조.

## 1. 네 겹 잠금 정비 (프리장 → 정상 세션)

| # | 종전 잠금 | 변경 후 | 파일 |
|---|---|---|---|
| ① | `premarket_policy.enabled=false` | 스위치 + `approved_by`·`approved_at_kst`가 있으면 열림(현재는 승인 전이라 닫힘) | `config/premarket_policy.json`, `core/session_routing.py` |
| ② | `buildup.HARD_LOCK`의 `premarket_enabled == False` | **"승인 없는 활성화 금지"**로 재설계 — 하드락 잠금은 유지하되 승인된 활성화는 통과 | `core/buildup.py` |
| ③ | `live_session_gate`의 `phase != "REGULAR"` | 세션 계획의 허용 단계(정규장 + 승인 시 프리장/애프터마켓)로 판정 | `core/live_session_gate.py` |
| ④ | `autotrader`의 `phase != "REGULAR"` 차단 | `session_plan` 기반 — 진입·청산 모두 허용 단계에서 실행, 호가유형 자동 라우팅 | `core/autotrader.py` |

**호가유형 라우팅**: 정규장 `00` · 프리마켓 `61`(price>0 필수) · 애프터마켓 `62`(정책 `after_hours.enabled` 필요). `OrderRouter.place(order_type=...)`가 NH `ahi_nmn_pr_tp_cd`로 전달하고, **세션과 호가유형이 어긋나면 디스패치 전에 차단**(`BLOCKED_PHASE`).

**프리장 얇은 호가 방어** (`session_routing`):
- 지정가 슬리피지 상한 **15bp**(정규장 20bp), 매도 −15bp
- 1회 예산 **$60**·세션 매수 **2건** 상한(정규장 예산과 별도)
- 진입 창 **ET 04:00~09:00**(개장 동시호가 30분 회피), 시세 신선도 **6분**
- 추격 금지: 전일 종가 대비 **+1.0% 초과 상승 시 매수 금지**
- 허용 신호: `DIP_SWING_DAILY`만(헤지는 프리장 진입이 유의하게 나빠 제외 — §3-②)

## 2. 판단 결과 → 주문 연결 (`decision_attribution`)

`core/decision_attribution.py`가 다섯 갈래로 남긴다(상태 파일 `decision_attribution`, 주문 컨텍스트 `context.attribution`).

| 갈래 | 출처 | 실전 반영 방식 | 기본값 |
|---|---|---|---|
| `macro8` | `config/macro_leading_policy.json` + `data/macro_leading.json` | `risk_scale`에 곱함(진입 예산) | shadow = 계수 1.0 |
| `regime_v2` | `config/regime_policy.json`(신규) + `core/regime_model.py` | CRISIS 신규 롱 차단·RISK_OFF 계수 | shadow(차단·계수 없음) |
| `buildup` | `data/strategy_buildup.json` + `data/strategy_params.json` + `data/collab_cycle.json` | 진입 필터(RSI2·%B·RVOL) 래칫 → 다음 사이클부터 집행. 마지막 10인 협업 사이클(번호·검증 결과)도 함께 기록 | 실제 적용 중 |
| `risk` | `config/risk_guardrails.json` + `live_execution_gate.json` | VIX 계수·예산 binding·현금 마지노선·매도전용 | 실제 적용 |
| `session` | `config/premarket_policy.json` + `core/session_routing.py` | 단계·호가유형·프리장 조건 | 정규장 |

추가로 빌드업 장부를 실행 정책과 **동기화**한다(`buildup.reconcile_execution`, `run_collab_cycle` 4단계에서 매 사이클 실행):

- OOS 게이트를 통과한 안건은 `HELD`(막힌 일)가 아니라 **`RECOMMENDED` + 실행 스위치 경로**로 남는다.
  예: `H-0009 REGIME_ADAPTIVE_DIP_V1` → `config/regime_policy.json → enabled (+approved_by)`.
- 스위치가 켜진 것이 확인되면 장부를 **`APPLIED`**로 맞추고 개선 이력(`I-xxxx`)을 자동 기록한다.
- 게이트 미통과 안건은 건드리지 않는다(측정 결과 보존).

## 3. 백테스트 ① — CUBE-M8 × V2 레짐 결합

일봉 2022-01-03 ~ 2026-11-17, 왕복 **0.24%**, 신호는 전일 완료봉·진입은 다음 봉 시가, 같은 봉 SL·TP 동시 도달은 손절 우선. M8 복합 z는 FRED 원계열(로컬 캐시)로 **발표 지연을 반영해 다시 계산**(1,255거래일 전부 유효, 성분 21/21).

| 변형 | 구간 | 수익률 | Sharpe | MDD | 승률 | 건수 | 건당 EV | PF |
|---|---|---|---|---|---|---|---|---|
| V2 · shadow(현행) | IS | 4.71% | 0.19 | −12.84% | 49.1% | 53 | 0.43% | 1.11 |
| | **OOS** | **36.34%** | **1.64** | **−8.35%** | 60.5% | 38 | 4.48% | 2.86 |
| | 전체 | 43.23% | 0.73 | −12.84% | 53.8% | 91 | 2.12% | 1.66 |
| V2 · linear | IS | 4.48% | 0.20 | −11.00% | 49.1% | 53 | 0.43% | 1.11 |
| | **OOS** | **25.74%** | **1.66** | **−5.79%** | 60.5% | 38 | 4.48% | 2.86 |
| | 전체 | 31.57% | 0.69 | −11.00% | 53.8% | 91 | 2.12% | 1.66 |
| V2 · binary | IS | 3.13% | 0.14 | −12.87% | 47.9% | 48 | 0.33% | 1.08 |
| | OOS | 8.67% | 0.90 | −4.52% | 58.8% | 17 | 2.51% | 1.98 |
| | 전체 | 12.14% | 0.32 | −12.87% | 50.8% | 65 | 0.90% | 1.25 |
| V2 + CRISIS 롱 금지 · linear | OOS | 15.31% | 1.16 | −5.82% | 55.9% | 34 | 3.28% | 2.22 |
| V1(기존 DIP) · shadow | OOS | 39.26% | 1.60 | −8.31% | 58.1% | 43 | 4.16% | — |

M8 선형 계수는 평균 **0.73**(0.35~0.94), 이진은 1,255일 중 **499일을 현금**으로 돌렸다.

**읽는 법**
- **linear는 위험조정 성과를 개선한다** — OOS MDD −8.35% → **−5.79%**, Sharpe 1.64 → **1.66**, 대신 총수익 36.3% → 25.7%(기회비용 약 10.6%p).
- **binary는 쓰지 말 것** — 전체 수익 43.2% → 12.1%, 건당 EV 2.12% → 0.90%. 스위치가 잦아 체결·슬리피지 위험이 실현되는 전형이다.
- **CRISIS 롱 전면 금지는 이 구간에서 손해** — OOS 22.98% → 15.31%(linear), MDD 개선 없음. 규칙으로 강제하지 않는 편이 낫다.
- 결론: M8을 켠다면 **linear만**, 그것도 "총수익 −10%p를 MDD −2.6%p와 바꾸는" 선택임을 알고 켠다. 동훈님이 13:51에 고른 shadow 유지는 이 표와 일관된다.

## 4. 백테스트 ② — 프리장 개입 조건별 기대값

프리장 포함 60분봉 **729세션(2023-12-20 ~ 2026-11-17)**, IS/OOS 컷 2026-01-02, 왕복 **0.44%**. 신호는 전일 완료 일봉 DIP(RSI2≤20·%B≤0.15·RVOL≥1.0), 진입은 해당 시각 봉 시가, 청산은 15:30 봉 종가. 같은 봉 진입·청산만 측정(진입 시각 효과만 분리).

### ① 진입 시각별(SOXL·TQQQ, 신호 126건)

| 진입 | 동일 세션 청산 EV | t | 1세션 보유 EV | t | PF(1세션) |
|---|---|---|---|---|---|
| 04:00 ET | +0.436% | 0.52 | +0.890% | 1.01 | 1.27 |
| 06:00 ET | +0.477% | 0.57 | +0.943% | 1.06 | 1.29 |
| 08:00 ET | +0.615% | 0.74 | +1.057% | 1.25 | 1.35 |
| 09:30 ET(정규장 시가) | +0.201% | 0.26 | +0.611% | 0.80 | 1.20 |

→ 프리장 진입이 정규장 시가보다 **+0.23%p(전체, t 0.72) / +0.93%p(OOS, t 1.59)** 좋지만 유의하지 않다.

### ② 조건별 (04:00 진입, 1세션 보유)

| 조건 | n | 전체 EV | t | OOS EV | OOS t | OOS Δ vs 정규장 시가 |
|---|---|---|---|---|---|---|
| 조건 없음 | 126 | +0.890% | 1.01 | +2.223% | 1.47 | +0.957 (t 1.64) |
| 추격 금지(갭 ≤ +1%) | 67 | **+1.736%** | 1.55 | **+2.018%** | 1.13 | +0.850 (t 1.01) |
| 갭 하락 매수(갭 ≤ 0) | 46 | **+1.906%** | 1.25 | **+2.133%** | 0.83 | +1.097 (t 0.92) |
| 전일 레짐 RISK_ON·NEUTRAL | 74 | −1.347% | −1.40 | +0.427% | 0.25 | +0.445 |
| 전일 VIX 16~24 | 88 | −0.367% | −0.44 | +0.656% | 0.42 | +0.586 |

### ③ 헤지(SOXS·SQQQ) — 프리장 진입 금지 근거

| 진입 | 전체 EV | t | OOS EV | OOS t | 정규장 시가 대비 |
|---|---|---|---|---|---|
| 04:00 | −0.706% | −1.75 | **−1.934%** | **−2.34** | −0.429 (t −2.12) |
| 09:30 | −0.277% | −0.76 | −1.049% | −1.40 | 기준 |

→ 헤지를 프리장에 미리 사는 것은 **개장까지 기다리는 것보다 유의하게 나쁘다**(OOS t −2.02). 그래서 `allow_sources`에서 헤지를 빼고, 헤지는 정규장 개장 후에 진입한다.

**결론**: 어떤 조건도 **OOS t ≥ 2.0**을 넘지 못했다. 가장 근접한 것은 "DIP 신호 + 추격 금지 + 1세션 보유"(IS +1.74%, OOS +2.02%, t 1.13). 정책의 `enable_requirements`대로 **자동 개입은 보류**하고, 표본이 쌓이면(신호 OOS 100건 이상) 재검증한다. 프리장 방어장치는 실측이 아니라 지정가·예산·건수·신선도로 건다(60분봉 프리장 거래량의 85.7%가 0으로 기록돼 유동성 필터는 데이터로 검증 불가).

## 5. 즉시 개입 사용법

```bash
# 미리보기(기본) — 주문 없음, 판정만
python3 scripts/intervene_trade.py --ticker SOXL --side BUY --qty 1
# 실주문
python3 scripts/intervene_trade.py --ticker SOXL --side BUY --qty 1 --execute --reason "M8 판단 반영"
# 보유 청산(월요일 SQQQ 개장 익절과 별개로 급히 정리할 때)
python3 scripts/intervene_trade.py --ticker SQQQ --side SELL --qty 1 --execute
# 클라우드(러너)에서 — GitHub Actions 수동 실행
gh workflow run intervene_trade.yml -R stevepark310/us-capital-preservation \
  -f ticker=SOXL -f side=BUY -f qty=1 -f dry_run=false -f reason="즉시 개입"
# 현재 세션·게이트·보유 상태
python3 scripts/intervene_trade.py --status
```

주문 직전에 다시 읽는 것: 기간한정 게이트(세션일·만료·매도전용·매수한도) → 단계별 호가유형 → 유니버스 → 물타기 금지 → 예산(현금 − $150) → 프리장 창·추격·건수 → 지정가 슬리피지 상한. 결과는 `data/intervention_log.jsonl`에 남는다.

**워크플로 커버리지**: `market_autotrade` 크론을 UTC `8-21`시로 넓혀 프리장(08:00–14:30 UTC)과 정규장(13:30–21:00 UTC)을 모두 덮는다. 프리장은 정책이 닫혀 있으면 `SKIPPED_OFF_HOURS`로 끝나 주문이 나가지 않는다.

## 6. 월요일(11/23) 사용 시나리오

| 시각(KST) | 일어나는 일 | 상태 |
|---|---|---|
| 18:00~23:30 (프리장) | 프리장 사이클은 돌지만 정책이 닫혀 있어 주문 없음 | 스위치 off(승인 대기) |
| 23:30 | 정규장 개장, **SQQQ 1주 시장가 익절**(`open_exit_plan`, 11-21 10:41 승인) | 승인됨 |
| 개장 후 10분 사이클 | 청산 규칙·손실 하드캡·V2 shadow·M8 shadow 판단 | 승인됨 |
| 아무 때나 | `intervene_trade.py --execute`로 **매도** 개입 가능 / **매수는 게이트가 매도전용이라 차단** | 매수는 승인 필요 |

## 7. 동훈님이 결정·승인해야 할 것

1. **매수 게이트 한도(월요일)** — 지금 게이트는 `sell_only: true`(매수 0건·$0). 매수 개입/자동매수를 쓰려면 `max_buy_order_usd`·`max_session_buy_usd`·`max_session_buy_orders`를 채운 새 게이트 승인이 필요하다(권고: 65 / 120 / 2, 11-19 게이트와 동일).
2. **프리장 개입 승인 여부** — 승인 시 `config/premarket_policy.json`의 `approved_by`·`approved_at_kst`를 채우고 `enabled: true`. 조건은 위 §4의 "DIP + 추격 금지 + 1세션 보유"로 고정돼 있고, 1회 $60·세션 2건·15bp 상한이 함께 걸린다. **권고: 자동 개입은 아직 보류**(OOS t 1.13), 다만 "사람이 지시하는 즉시 개입"은 승인 시 프리장에서도 가능해진다.
3. **M8 활성화** — shadow 유지(현행) / linear / binary 중 선택. 권고: shadow 유지 또는 linear(총수익 −10%p ↔ MDD −2.6%p).
4. **V2 레짐 활성화** — `config/regime_policy.json`의 `enabled: true` + `approved_by`. V2는 이미 IS/OOS 게이트를 통과한 안건(H-0009)이며, 켜면 CRISIS 롱 차단·RISK_OFF 0.5 계수가 걸린다. **권고: 보류**(§3에서 CRISIS 강제 차단이 손해였음).
5. **즉시 개입 워크플로 설치** — `.github/workflows/intervene_trade.yml`(템플릿 `execution/intervene_trade.yml.template`)을 저장소에 반영해야 GitHub UI/`gh`에서 버튼으로 실행할 수 있다. `workflows` 권한이 막히면 `bash scripts/install_workflows.sh`를 권한 있는 곳에서 1회 실행.

## 8. 남은 위험·한계

- 프리장 호가·체결 품질은 데이터로 검증할 수 없다(60분봉 거래량 대부분 0). 지정가 슬리피지 상한·예산·건수·추세 신선도로만 방어한다.
- V2/M8 계수를 **동시에** 켜면 계수 하한이 0.175까지 내려가 1주 가격이 예산을 넘을 수 있다. 둘 다 켜는 경우 예산 하한·최소 1주 규칙을 재점검해야 한다.
- `decision_attribution`은 이번 사이클의 판단을 남기지만, 체결 후 `_apply_fill`이 브래킷에 attribution을 복사하지는 않는다(주문 레코드에 남는다). 필요하면 다음 단계에서 브래킷에도 복사한다.
- 백테스트는 3년 표본이며 3배 레버리지 ETF의 변동성 감쇠가 구간에 좌우된다. 프리장 진입 시각 효과는 OOS 표본 48건으로 얇다.
