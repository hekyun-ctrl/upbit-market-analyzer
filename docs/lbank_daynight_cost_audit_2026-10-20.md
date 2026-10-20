# 공개 주야간 논문: BTC 전체 결과표와 원문 비용식 검산

2026-10-20. 판정: **원문 표에 조건부 양수 결과는 있으나, 검증된 LBank 수동 알람으로 승격하지 않는다.** 이번 완료 단계는 실제 공개 결과표의 비용 산술 재현이다. 원본 가격 재생, 원문 백테스트 전체 복제, LBank 전체 비용·실제 체결 검증은 아니다. 양수 결과도 삭제하지 않으며 전략군 전체를 무효라 하지 않는다.

## 원전과 실행 범위

- [논문 원문](https://www.mdpi.com/1911-8074/19/9/692), DOI 10.3390/jrfm19090692, 2026-09-06 발행. 원문 페이지 직접 열기는 429였으며 출판사 검색 색인의 제목·발행일·전체 표본 선택 설명을 확인했다. 이를 원문 전체 정독으로 세지 않는다.
- [저자 코드·전체 표 고정본](https://github.com/wzf01195010-png/Crypto-day-night-effects/tree/5693993e108f2b3668bd21b1dd36ae31f79d8fcd), 원전 규칙 버전 `5693993e108f2b3668bd21b1dd36ae31f79d8fcd`.
- 주야간 12시간, 각 세션 cash/long/short/직전 같은 세션 수익률의 부호/반대 부호의 25조합, UTC 경계 12개. 임의 손절·TP1·조기 진입이나 새 조합을 만들지 않았다.
- [규약](../research/continuation/daynight_cost_protocol_20261020.json)과 코드는 계산 전 [defd038 체크포인트](https://github.com/hekyun-ctrl/upbit-market-analyzer/commit/defd03877b9f1fb14f39486d6683da911d27fe61)에 고정했다. 선호 BTC 0/1bp 결과는 이미 보았으므로 맹검 또는 독립 전략 검증이 아니다. 첫 실행은 비용 변화 0인 항에도 미사용 로그를 계산하는 `math domain error`로 중단됐다. 지수가 0인 항을 계산하지 않는 수치 구현만 고쳤고 규칙·비용·검사 기준은 유지했다. 성공 계산은 05:51:59 UTC였다.
- 검토한 원전 순수 정의 4개만 AST로 추출해 실행했다. 원전 최상위 코드, 자료 다운로드·최적화·실계좌 스크립트는 실행하지 않았다. 실제 시장 가격으로 새 거래를 만든 횟수는 0이다.

## 실제 계산 결과

원문 전체 표 1,800행 중 BTC 900행(300조합 × 비용 0/1/2bp)을 빠짐없이 검사했다. 핵심 표의 BTC 525행도 전략 이름·UTC 경계·비용으로 결합해 일치했다. 두 표의 전략 ID는 시작 번호가 다르므로 ID로 잘못 결합하지 않았다.

원문의 비용식은 `1 + net = (1 + position × return) × (1 − c × abs(position − previous_position))`이다. `position`은 −1/0/+1, `c`는 편도 bp/10,000이다. 방향 전환 수 N, 회전량 합계 T이면 한 단위 변화 횟수는 `2N−T`, 두 단위 변화 횟수는 `T−N`이다. 따라서 원문 모형에 한해서 `W(c)=W(0)×(1−c)^(2N−T)×(1−2c)^(T−N)`가 성립한다. 이는 해당 모형의 요약표 조건부 재계산이지 실제 거래 원금·펀딩 현금흐름의 복원이 아니다.

원문 BTC 900행 최대 상대 오차는 **3.34×10⁻¹³**, 핵심/전체 표 525행 중복 비교 오차는 **0**이었다. 별도 표준 라이브러리 Decimal 검산은 **3,307검사**, 구현 회귀 검사는 **9개**, 원문 함수를 쓴 합성 비용 대조는 **12사례**를 통과했다. 합성 사례를 거래 성과로 세지 않는다.

| 편도 비용 입력 | 시작값 100 대비 양수인 BTC 조합 | 음수 조합 |
|---|---:|---:|
| 원문 0bp | 104 / 288 | 184 |
| 원문 1bp | 95 / 288 | 193 |
| 원문 2bp | 76 / 288 | 212 |
| 진단 8bp | 14 / 288 | 274 |
| 진단 16bp | 12 / 288 | 276 |

분모에서 12개 cash/cash만 제외했다. 300개 전체 계산과 모든 음수 조합은 CSV에 남겼다. **16bp 양수 12개는 모두 계속 롱을 보유하고 포지션 변화가 단 1회인 long/long**이다. 이들을 서로 다른 12개 우수 알람 전략이나 하루 1건 기회로 세지 않는다. 300개 역시 같은 자료를 공유하는 상관된 과거 조합으로 독립 표본이 아니다.

저자가 전체 표본에서 선택한 BTC 08:00 UTC Reversal/Reversal을 그대로 보면 다음과 같다. 이번 계산에서 다시 고른 승자가 아니다.

| 항목 | 원문 모형 조건부 계산 |
|---|---:|
| 자료 날짜 | 3,652일, 2016–2025 |
| 포지션 변화 사건 N / 회전량 T | 3,743 / 7,485 |
| 비용 0bp 최종값, 시작 100 | 95,015.459365 |
| 비용 1bp 최종값 | 44,946.134134 |
| 비용 2bp 최종값 | 21,258.145693 |
| 진단 8bp 최종값 | 237.222778 |
| 진단 16bp 최종값 | 0.586603 |
| 원문 비용식의 손익분기 편도 비용 | 약 9.1521bp |

8bp는 기존 연구의 편도 수수료6bp+슬립2bp 예산을 원문 식에 넣은 진단, 16bp는 그 두 배다. 그러나 원문 식에는 펀딩·정확한 명목금액별 수수료·실제 호가 체결·불리한 수동 지연이 없으므로 **기본 전체 비용 통과나 두 배 전체 비용 게이트의 정식 측정값으로 사용하지 않는다.** 높은 원문 저비용 양수를 숨기지도, 손익분기만으로 실전 수익을 인증하지도 않는다.

## 채택하지 않는 근거와 미확정 항목

1. 저자 README는 Kraken 원본 CSV를 공개하지 않았고 원본 출처·무거래 시간 처리·재배포 권리를 독립 확인해야 한다고 명시한다. [Kraken 공식 OHLCVT 안내](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data)는 2026-10-12 갱신본에서 무거래 구간에 봉이 없음을 설명하고 대용량 분할 원본과 체크섬을 제공한다. 공식 자료의 존재를 확인한 것이며 저자의 정확한 입력 CSV/SHA·시간별 연속성·가격 재현은 확보하지 못했다. 체크섬 URL은 웹 도구에서 열리지 않았고 약10GB 분할 자료를 검증 계획 없이 다운로드하지 않았다. 다른 거래소 가격을 Kraken으로 바꾸지 않았다.
2. 원전 시각은 봉 시작 라벨과 시간 내 마지막 가격으로 구성한 세션 종가다. 세션 수익률을 전일 같은 세션으로 shift하는 동작은 합성 prefix 대조에서 미래 추가 변경 0이었다. 하지만 이는 실제 봉 공개 시각·그 가격에서 즉시 수동 체결 가능성을 증명하지 않는다. 원전은 불완전 세션을 제외하며, 주 계산 코드의 lag가 남은 날짜 사이를 건널 수 있다. 실제 입력이 누락됐다고 단정한 것이 아니고, 공개 원본 없이 누락 여부를 확정할 수 없다는 뜻이다. 통계 스크립트의 연속 날짜 assert와도 구분했다.
3. 원전의 `Number_of_Trades`는 포지션 변화 사건 수이지 종료 포지션 수가 아니다. 3,743/3,652≈1.025를 일평균 진입 알람으로 사용하지 않는다. 종료 장부·기회일 비율·포지션별 초기 위험 R·진입 직후 역행·TP1 선도달·5분 내 긴급 후속은 미측정이다. 마지막 포지션은 평가 가격으로 표시되며 임의 강제 청산 수익이나 추가 청산 비용을 만들어 넣지 않았다.
4. 저자 공개 추론 표의 BTC 9행(전체·전반·후반 × 0/1/2bp) 모두, buy-and-hold 대비 일 로그수익 차이 95% 구간이 0을 포함한다. 예를 들어 전체 1bp는 −15.9203~20.9441bp다. 이는 절대 순기대 R의 구간이 아니므로 우리의 기대수익 하한으로 재명명하지 않는다. 원문은 5,000회 stationary bootstrap·평균 블록7일·제한된 Holm 그룹을 사용하지만 전체 표본 25×12 선택 문제까지 해소했다는 증거는 아니다.
5. BTC 단독 자료를 사용해 타자산 생존 종목의 포트폴리오 이익을 옮기지는 않았다. 그렇다고 단일 자산·전체 표본 선택의 편향이 사라지는 것은 아니다. 2022 및 2023–2025를 처음 보는 순방향 자료라고 하지 않는다. 원문 양수와 음수를 모두 보존하며 한 해 손실로 기법 전체를 무효라 하지 않는다.

순기대수익 95% 하한·PF·낙폭 R·TP1 Wilson·긴급 후속·실제 알람 빈도·native LBank 호가/공개시각/수동 체결·규칙 고정 후30일/100종료는 모두 미충족/미측정이다. 관련 값은 report에서 null 또는 false다. 원전에는 손절/TP1의 수동매매 계약이 없어 임의로 추가해 합격시키지 않는다. 전향 수집은 시작하지 않았고 실행 사이 수집기가 계속 작동한다고 주장하지 않는다.

## 재현과 보존

입력 원본은 고정 저자 Git에 그대로 있으며 보고서에 24파일의 Git blob SHA와 SHA256을 저장했다. 주요 SHA256:

- 원전 `btc_eth_backtest.py`: `70a2d1b4432d6a7e8e2e06bd6bf104d5b2b2ee4b57b240274f4cc1f56e89b219`
- 전체 결과표: `907932cb694641c15a04275a17727ec7351a4714f183259fb5f703af03bb5059`
- 핵심 결과표: `ac79eebace8fb952fcb0705b083ffa4fc18b3d96862f9b640ba68f2675034f92`

```bash
git clone https://github.com/wzf01195010-png/Crypto-day-night-effects.git source_daynight
git -C source_daynight checkout --detach 5693993e108f2b3668bd21b1dd36ae31f79d8fcd
python -m unittest discover -s research/continuation -p test_daynight_cost.py -v
python research/continuation/audit_daynight_cost.py --repo source_daynight --output research/continuation/results/daynight_cost_20261020
python research/continuation/verify_daynight_cost.py --repo source_daynight --output research/continuation/results/daynight_cost_20261020
```

Python・numpy・pandas 필요. 시각을 제외한 표와 검산 수치는 재현 가능하다. [전체300조합](../research/continuation/results/daynight_cost_20261020/all300_BTC_conditional_costs.csv), [900행 대조](../research/continuation/results/daynight_cost_20261020/all900_BTC_cost_reconciliation.csv), [보고서](../research/continuation/results/daynight_cost_20261020/report.json), [별도 검산](../research/continuation/results/daynight_cost_20261020/independent_verification.json), [원전 추론 전체9행](../research/continuation/results/daynight_cost_20261020/author_BTC_all_inference_periods.csv).

## 다음 유한 단계 및 운영

이 원전은 현재 공개본만으로 수동 알람 교체 근거가 되지 않으므로 원자료 전체 재다운로드·300조합 최적화·임의 출구 추가로 반복하지 않는다. 별도 원전 `Silentbilly/trend-pullback`의 고정본·실제 빈 자료 디렉터리·미공개 결과 여부를 다음 체크포인트에 남겼다. 다음은 해당 원본의 신호/초기 위험/다음 시가/보호 주문·비용 계약을 먼저 검사하고, 적합하면 원래 단일 설정과 이미 관측한 자료를 고정하여 비용 후 거래를 실제 계산하는 유한 단계다. 양수 증거가 없는 현 상태에서 합격 후보로 세거나 무리한 전체 최적화를 하지 않는다. 다른 새 링크 `mantasbil/Thesis-Algo-Trading`은 원본 README/전체 트리에서 XAUUSD 연구임을 확인했으므로 BTC 장부로 전용하지 않는다.

운영862339d·배포181eb91b SUCCESS는 읽기 전용 확인했고, 14:45:19KST에 native_ready와 기존 정책의 새 봉 포함 심사·후보0을 확인했다. 실제 거래소 공개시각과 발신 전체 지연은 미측정이다. 운영 재시작·새 대체 배포·손실/증거금 상한 변경은 없었다. 후속 작업과 누적 기록을 이어가되 수익·완료일을 약속하지 않는다.
