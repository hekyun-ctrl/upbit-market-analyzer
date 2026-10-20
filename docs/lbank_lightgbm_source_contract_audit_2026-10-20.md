# 공개 15분봉 LightGBM 원전의 실행 계약 감사 — 2026-10-20

## 결론과 범위

검토하던 외부 원전 `djienne/ML_Trading_Strategy_Crypto_BTC_LightGBM_Jansen`의 **공개 버전은 대체 알람 채택 근거에서 제외**한다. 모델·원본 예측·거래 장부가 공개 트리에 없었고, 검토한 신호/비용/청산 함수에서 재현 가능한 계약 불일치가 확인됐다.

이는 이 외부 버전의 코드 감사다. **현재 운영 LBank 알람의 손실 원인을 확인했다는 뜻도, 모든 머신러닝 전략이 손실이라는 뜻도 아니다.** 원전의 과거 성과를 시장 자료로 다시 계산하거나 새 모델을 학습하지 않았다.

- 원전 고정 커밋: [1274eac3](https://github.com/djienne/ML_Trading_Strategy_Crypto_BTC_LightGBM_Jansen/tree/1274eac3f8e02ddce6be7ff3b48887a971932f35)
- [입력 12파일의 URL·바이트·Git blob·SHA256](../research/continuation/lightgbm_source_manifest_20261020.json), [완전한 트리 56항목](../research/continuation/results/lightgbm_source_20261020/source_tree.json)
- [검사 규약](../research/continuation/lightgbm_source_audit_protocol_20261020.json) 고정: 2026-10-20 09:57:35 KST
- 규약 최초 체크포인트: `063cbc79e96fb7b4a763f0917d91d09148c4f62e`
- 실제 보고서를 만든 코드 체크포인트: [f6bdde78](https://github.com/hekyun-ctrl/upbit-market-analyzer/commit/f6bdde78e3e51449c9331fa1d8401d863d30a0f1)
- 실제 계산: 2026-10-20 **10:02:21 KST**; Python3.12.14 / NumPy2.3.5 / pandas2.2.3

## 실제 실행 방식

원전 모듈 전체를 import하지 않았다. 읽고 해시를 확인한 **19개 순수 함수**만 AST로 선택해 numba 장식자를 제거하고 고정 합성 반례·대조군에서 실행했다. 함수 본문·거래 규칙·수수료·반례를 성과에 맞춰 수정하지 않았다. NumPy/pandas의 설치된 내부 의존성 로딩만 허용한 검사 래퍼이며 OS 보안 샌드박스라는 뜻은 아니다.

Freqtrade·재학습기·외부 모델·사용자 계좌·주문·알림은 실행하지 않았다. 16개 검사는 아래 결함과 정상 대조군을 재현하는 검사이지, 전략 성과가 통과했다는 검사가 아니다. 초기 래퍼의 순수 내장 함수/NumPy 의존성 허용 누락으로 두 번의 사전 검사가 종료됐고, 각각 완료 검사 수는0이었다. [기록](../research/continuation/results/lightgbm_source_20261020/harness_preflight.json)에 남겼으며, 원전 결함으로 세지 않았다. 이를 보완한 코드에서 16개 검사가 통과한 뒤 실제 결과 파일을 생성했다.

## 재현한 결과

| 검사 | 고정 입력에서 실제 나온 결과 | 해석 |
| --- | --- | --- |
| 첫 보유 봉에서 손절 | 원전 모형의 손실 −20%는 총손익에 남음. 그러나 수수료 전환 수와 종료 장부 행은 각각0 | 진입·손절 두 비용 이벤트와 종료 거래가 집계에서 빠짐. 손실 자체가 총손익에서 사라진다고 해석하면 안 됨 |
| 같은 신호에 수수료 두 배 | 0.03%와0.06% 양쪽 모두 위 반례의 순손익이 −20%로 같음 | 누락 비용 이벤트의 재현일 뿐 전체 시장의 두 배 비용 검증이 아님 |
| 정상적인 진입→청산 대조군 | `Total Trades=2`, 종료 장부는1행; 순손익은 −0.059991% / −0.119964% | 보고 필드는 종료 거래 수가 아닌 포지션 전환 수. 원전 성과표 숫자를 종료100건으로 읽을 수 없음 |
| 월말 무포지션에서 새 진입 | 1월31일23:45의 q100이 진입을 만들고2월 첫 봉에도 보유 | 기존 포지션 청산만 처리하고 같은 월말 봉의 신규 진입을 막지 못하는 반례 |
| 보유 중 결측 순위 | q=`[100, 미상,95,50]`에서 미상 봉의 목표 포지션0·청산 플래그true | 결측은 아무 행동을 하지 않는다는 설명과 다름 |
| 손절 후 재진입 제한 | 모형은 진입을 잠그지만 live용 진입 플래그는 계속true | 신호 계약 불일치. 실제 Freqtrade 주문·재진입 체결을 검증한 것은 아님 |
| 봉 중 하락 후 회복 | 시가/종가100·저가75의 합성 봉에서 명목 손절80을 지나도 손절 플래그0 | 시가→종가만 보는 원전의 문서화된 근사. 실제 손절 체결가를 가정하지 않음 |
| 보유 중 봉 사이 가격 변화 | 보유 진입100→청산90인데 각 봉 시가=종가라 원전 곱셈 수익률0 | 봉 사이 가격 변화를 생략하는 모형의 반례. 시장에서의 발생 빈도·과거 총성과 영향은 미상 |

### 정상적으로 통과한 대조군도 보존

고정한 800개 합성 입력 중 첫600개 뒤에 미래200개만 추가했다.

- rolling 순위의 과거 변경0건.
- 20개 지표의 과거 셀 변경0건(오차 허용1e−12).
- 1봉 실행 이동은 기대한 `[0,0,1,1]`과 같았다.

이 제한된 대조군은 인과성의 긍정적 근거다. 학습·모델 선택·모든 입력 경로의 누출을 없다고 증명하지는 않는다. 원전은 여러 설정을 같은 OOF 자료에서 선택했다고 설명하며, 별도 미관측 성과의 증거는 확보하지 못했다.

## 모델·자료·위험 계약 확인

현재 메타데이터로 안내된 `model_info.json`은 고정 커밋에서404였고, 전체 트리에도 해당 모델·예측 이력은 없었다. 따라서 문서에 쓰인 학습 시각은 저자 설명이지 직접 확인한 모델 시각이 아니다. 저자가 보고한 모의 거래/재생 일치 수치도 원본 거래 장부로 재검산하지 못했다. 문서의 과거 수익률을 현재 모델 또는 LBank 실현 성과로 옮기지 않는다.

확인된 코드 기본값은15분·BTCUSDT·롱·12개월 학습·순위100진입/90청산·수수료0.03%·손절−20%였다. 이 손절률은 사용자 계좌의20% 손실이라는 뜻은 아니지만, 사용자 운영의 위험·TP1 계약으로 검증하거나 도입 승인받은 것도 아니다. 현재 코드 경로에서 독립적인 고정 TP1 계약, 펀딩·미끄러짐·native 체결/지연 검증은 확보하지 못했다. 손절을 넓히거나 포지션을 키워 채택하려 하지 않았다.

2022~2026년9월을 새로운 독립 구간으로 표시하지 않았다. 이번 입력은 **합성 진단 자료**이며 새 시장 성과 표본이 아니다. 역사 자료 공백을 임의 청산·0수익으로 채우는 작업도 없었다.

## 원본 복구·별도 검산·재현

[원래 보고서](../research/continuation/results/lightgbm_source_20261020/report.json)에는 반례 입력/출력, 실제 실행 코드 SHA와 함수별 원전 행·AST 해시가 있다. [별도 표준 라이브러리 검산](../research/continuation/verify_lightgbm_source_audit.py)에서 Decimal 비용과 이벤트 수 등10개 대조를 완료했다. 같은 연구자의 별도 산술 구현이지 외부 기관의 검증은 아니다.

임시 원본을 재사용하지 않고, 고정 URL에서12파일을 새로 내려받아 해시를 확인하고 다시 실행했다. [복구 실행 보고서](../research/continuation/results/lightgbm_source_20261020/recovered_report.json)는 시작·종료 시각을 제외한 모든 필드가 같았다. 결과·입력 해시는 [delivery.json](../research/continuation/results/lightgbm_source_20261020/delivery.json)과 [복구 대조](../research/continuation/results/lightgbm_source_20261020/recovery_audit.json)에 남겼다.

빈 입력 경로를 지정하면 원본부터 복구할 수 있다. 기존 파일/보고서는 덮어쓰지 않는다.

```bash
python research/continuation/audit_lightgbm_source_contract.py --fetch-sources /tmp/new-empty-lightgbm-source --output /tmp/new-lightgbm-report.json
ML_AUDIT_SOURCE_DIR=/tmp/new-empty-lightgbm-source python -m unittest discover -s research/continuation -p test_lightgbm_source_contract_audit.py -v
python research/continuation/verify_lightgbm_source_audit.py /tmp/new-lightgbm-report.json
```

수집 뒤 원본을 직접 지정한 실제 첫 실행과 저장 결과 검산 명령은 다음과 같다.

```bash
python research/continuation/audit_lightgbm_source_contract.py --source-dir <hash-verified-source-directory> --output research/continuation/results/lightgbm_source_20261020/report.json
python research/continuation/verify_lightgbm_source_audit.py research/continuation/results/lightgbm_source_20261020/report.json
```

## 승격 판정과 다음 단계

순기대수익·상관 고려95% 하한·PF·전체 비용 두 배·TP1 Wilson·진입 직후 실제 역행/5분 긴급 경고·기회 빈도·R 낙폭·native LBank 체결은 **미평가**다. 반례를 시장 빈도로 환산하거나 미상을0/통과로 채우지 않았다. 합격 후보·전향30일/100건 관측·운영 교체·배포는 없으며 기준도 바꾸지 않았다.

[추가 원전 선별](../research/continuation/source_screening_20261020_1007.json)에 낮은 빈도, 비용 후 근거 부재, 다중 종목/체결 모형 한계를 기록해 같은 소스를 다시 계산하지 않도록 했다.

다음은 별도 공개 BTC 복합 규칙의 [원전·원본 JSON 확인 지점](../research/continuation/rlxbt_combo_source_preflight_20261020.json)이다. 공개 설명에는 비교적 많은 거래가 있지만, 저자 로컬 자료 경로·미정의 특징량·수수료와 SL/TP 단위가 남아 있다. **규칙을 임의 추정하지 말고 공개 코드·특징량 정의·자료/장부·비용 단위부터 확인한다.** 공개 재현이 불가능하면 그 근거를 기록하고 다른 원전으로 이동한다. 사이트의 채택 라벨이나 과거 양수만으로 후보를 유지하지 않는다. 계정·유료 설치·저자 연락·외부 실행은 하지 않는다.

운영 코드·Railway·알람 발신·위험 한도는 이번에 변경하거나 재시작하지 않았다.
