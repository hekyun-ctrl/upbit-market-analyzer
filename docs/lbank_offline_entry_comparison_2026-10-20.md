# 현재 진입 규칙의 무발송 과거 재생 비교 — 2026-10-20

## 실제 완료한 범위
사용자 지시에 따라 실전 알람을 기다리는 대신 **실제 분봉을 순서대로 재생**했다. 규약 6cc4483, 실행 코드 63f2720, 한계 명시 bc62690을 결과 계산 전에 연구 브랜치에 저장했다. 기존 운영 소스 862339db2c4f69facc9ba6626e2826d475504174의 classify/confirm/alert 빈도 제한을 호출했다. 주문·알람 발송은 0건이다.

이것은 **초기 손절/TP1 최초 사건 진단**이다. 현행 목표 전 수익 보호와 부분 익절/러너 경로를 제외했으므로 운영 계정 수익이나 실제 긴급 경고 비율을 재현한 결과가 아니다. 현행 전체 전략이 이 수치 그대로 손실이라는 뜻도 아니다.

고정 구간은 UTC 2024-01-08 00:00부터 01-15 00:00 직전까지, 7일이다. 첫 완전 월~일 주간과 앞선 7일 준비 구간을 사용했다. 이 기간은 이미 사용한 개발 자료이며 독립/전향 검증이 아니다. 결과가 나쁜 주만 골라 모든 기법을 무효화하지 않는다.

## 고정 비교와 실제 결과
기준안과 수정안은 동일 비용·시점·자료를 쓴다. 수정안은 추세 되돌림 후보/확인에서 **기존 higher_direction_intact=true를 필수화**하는 단 하나의 탐색적 차이다. 새 점수·추가 대기·매개변수 최적화는 없다. 공개 원전에서 수익성이 검증된 대체 전략이라고 주장하지 않는다.

| 측정 | 기준안 | 상위 방향 필수안 |
|---|---:|---:|
| 모의 신호 / 종료 / 말단 미종료 | 17 / 16 / 1 | 16 / 15 / 1 |
| 비용 후 종료 손익 합계 | -2.6894R | -4.1457R |
| 종료당 평균 | -0.1681R | -0.2764R |
| 이익 합계/손실 합계(PF) | 0.7285 | 0.5815 |
| 동일 거래 비용 두 배 합계 | -7.5966R | -8.8193R |
| TP1 최초 도달 / 종료 | 5/16 | 4/15 |
| TP1 Wilson 95% 하한 | 14.16% | 10.90% |
| 조건부 2일 블록 평균R 95% 구간 | [-0.4849, 0.5435] | [-0.7976, 0.5086] |
| 종료 실현R 최대 낙폭 | 7.0150R | 8.4713R |
| 경과 UTC일당 모의 신호 | 2.4286 | 2.2857 |
| 평균 최초 5분 역행 | 0.1791% | 0.1823% |

두 안은 거래가 겹치므로 합계 31개를 독립 거래 31개로 세지 않는다. 방향 조건을 추가한 안은 이 구간에서 수익 거래 한 건을 제외했다. 단순 상위 방향 일치 필수화의 개선 근거는 얻지 못했으며 **운영에 적용하지 않는다**. 다른 구간/다른 정확한 진입 계약까지 무효라고 일반화하지 않는다.

## 자료·비용·시간 계약과 한계
- 공식 Binance USDT 선물 2024-01 분봉 ZIP 44,640행. SHA256 `21eeac04a76a7a35b10467e5e752fb2f8cff77cdeb57df6b50a23ce8d69bb190`. 분 간격 누락0, OHLC 정합 확인, 완료 5분봉만 집계했다.
- 원본: https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip
- 공식 펀딩 ZIP SHA256 `3e0d30870672aa8f0f937881056e3cfd55913ae5c780cd50b33f2763aa0ba58e`.
- 원본: https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip
- 이전 공식 manifest SHA와 대조했다. 코드의 native 어댑터 표시는 기존 함수 호출 인터페이스이며 **LBank 실제 가격·체결 자료로 바꾼 것이 아니다**.
- 현재 분봉 시가는 알려진 호가 대용, 과거 완료 1분봉360/5분봉250/완료15분 종가를 사용했다. 후보 확인은 다음 1분 사건으로 모델링했다. 실제 30초 확인/수동 체결 지연과 호가 잔량은 미검증이다.
- 편도 수수료 6bp와 슬립 2bp, 실제 Binance 펀딩률 및 정산 분봉 시가의 명목금액 근사. 비용 두 배는 수수료·슬립·펀딩 지급을 두 배, 펀딩 수취는 늘리지 않는다. LBank 실제 비용 실적은 아니다.
- R 분모는 원래 소스의 초기 손절 위험(수수료/손절 슬립/펀딩 예비비 포함)이다. 고정 분모로 두 비용 조건을 비교했다.
- 같은 분봉에서 손절/목표 둘 다 닿으면 손절 우선, 손절 갭은 불리한 시가. 자료 끝 미종료 각1건은 강제 청산하지 않았다. 모델 내 최대 동시 참조2건으로 낙폭은 계정 전체/시가평가 낙폭이 아니다.
- 블록 부트스트랩 5000회는 KST 2일 원형 블록과 경계 일부인 날짜를 포함한 8개 달력일이다. 7일의 작은 재사용 표본이며 전략 선택 편향을 보정하지 못한다.
- 실제 LBank 5분 내 긴급 경고율은 **null**이다. 모델 초기 손절 5분 내0 또는 진단 경고0을 실전 긴급0으로 표현하지 않는다. 생존 편향은 BTC 단일자산·고정기간이라는 제한이 있고 자산 선택 전체를 검증한 것은 아니다.

## 검산과 재현
별도 Decimal 현금 계산 62건(중복되는 두 안의 종료행31개), 최대 오차 5.13e-14R. 서로 다른 진입 시점16개에서 현재 분봉 H/L/C와 미래 봉을 바꿔도 과거 입력·classify 결과는 동일했다. **전체 시뮬레이션의 미래 추가 불변 검사까지 완료한 것은 아니다.**

연구 코드:
- [계산기](../research/continuation/offline_current_entry_compare_20261020.py)
- [별도 검산](../research/continuation/verify_offline_current_entry_compare_20261020.py)
- [종료별 전체 결과·원본SHA](../research/continuation/results/current_native_offline_20261020/report.json)
- [결과 전 고정 규약](../research/continuation/current_native_offline_protocol_20261020.json)
- [결과 전 범위 명시](../research/continuation/current_native_offline_clarification_20261020.json)

원래 운영 소스의 별도 checkout에서 연구 계산기를 `offline_current_entry_compare.py`, 검산기를 `verify_offline_current_entry_compare.py`로 복사한다. DATA는 기존 공식 manifest-1m/5m.json 및 해당 ZIP의 디렉터리다. 운영 import 의존성 설치 후 실행한다:
```sh
python offline_current_entry_compare.py --data "$DATA" --output comparison.json
python verify_offline_current_entry_compare.py --data "$DATA" --result comparison.json --output verified.json
```
실제 실행에서는 `PYTHONPATH=.:/workspace/scratch/ba9af914844b/lbank-test-deps`, DATA=`/workspace/scratch/ba9af914844b/binance-futures-verified`를 사용했다. 결과는 구현/수치 검산을 통과했으나 수익성 합격은 아니다.

## 원본 입력 확보용 별도 패치 — 미배포
[옵트인 capture 패치](../research/continuation/patches/entry_evidence_capture_v1.patch)를 운영 트리 cbf343b437ba1379dc2304f706db30b0f8746814에 작성했다. 기존 원래 #14 전체 분봉 스냅샷을 읽을 SSH/CLI/원격 실행 수단은 이 환경에 없다는 유한 접근 확인을 마쳤다. 누락 과거를 만들어내거나 연구 문서 저장을 위해 운영을 재시작하지 않았다.

공개 시장 입력을 사건 시점에 복제하여 제한 큐/압축 SHA 기록하며 계좌/주문/인증값은 수집하지 않는다. 환경변수 LBANK_ENTRY_EVIDENCE_ENABLED 기본 false. 큐 포화/기록 실패는 명시하고 진입/경고 실행을 막지 않는다. classify/assess_candidate/confirm_watch/calculate_plan/alert_gate/record_alert/net_pnl_per_btc의 AST가 원본과 같음을 검사했다. 349개 코드 검사(새6개 포함)가 통과했다. 이는 계측 패치의 검사이며 전략 성능 향상 건수로 세지 않는다. **패치 미배포·수집 미시작**이다.

## 이어갈 실제 단계
달력상 기다림에 앞서 과거 재생으로 수정안을 빠르게 걸러낸다. 다음 실행에서는 이 첫 주간을 재최적화하지 말고, 구간을 먼저 고정하여 동일 안의 2024-01 잔여 및 다른 연도/국면 비교를 수행한다. 동시에 현행 수익 보호/부분 익절의 실제 운영 함수 호출을 재현하는 출구 경로를 고정해야 전체 운영 성과라고 부를 수 있다. 이후 진입 위치의 장벽 통과/재지지 계약은 공개 원전의 정확 규칙과 비교해 결과 전에 고정한다.

현재 결과는 단순 방향 필터를 급히 붙일 근거를 부정하는 유한 진단이다. 기존 승격 기준은 유지한다. 짧은 과거 재생으로 검사가 빨라져도 이것만으로 순방향 30일/100종료·native LBank 체결 기준을 채운 것으로 바꾸지 않는다. 합격 대체 후보/전향 시작/운영 교체/배포는 없다.
