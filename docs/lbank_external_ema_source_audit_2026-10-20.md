# 공개 EMA 되돌림 전략: 성과표와 코드의 실행 감사

2026-10-20 KST. **이 원전 버전을 교체 근거에서 제외한다.** 실제 거래소 자료가 아닌 합성 가격에 더해 돌파 체결·손절·신호 소진 처리의 결함이 확인됐다. EMA 기법 전체의 무효를 뜻하지 않는다. 이전 자체 EMA 적응 전략을 다시 튜닝하거나 동일 시장자료의 손익을 반복 계산한 작업이 아니다.

## 원전 및 사전 고정

- [원전 저장소](https://github.com/tradewithbhai7777-creator/ema-pullback-momentum-strategy/tree/265209c39efc96eb97a35fd5cae45c0d6b868b30), 커밋 `265209c39efc96eb97a35fd5cae45c0d6b868b30`.
- [실행 코드](https://github.com/tradewithbhai7777-creator/ema-pullback-momentum-strategy/blob/265209c39efc96eb97a35fd5cae45c0d6b868b30/ema_pullback_momentum.py), [게시 장부](https://github.com/tradewithbhai7777-creator/ema-pullback-momentum-strategy/blob/265209c39efc96eb97a35fd5cae45c0d6b868b30/momentum_strategy_results.txt).
- 검토한 규칙: 1시간봉 EMA9/20, ADX14 또는 EMA 간격 증가, 간격0.15%, EMA 접촉·꼬리 거부·상하단30% 종가, 거부 봉 고저 돌파, 반대 극값 손절, EMA20 추적. 코드 목표는2.5R이고 README 설명은2R이다.
- [감사 규약](../research/continuation/ema_source_audit_protocol_20261020.json)과 [실행 코드](../research/continuation/audit_external_ema_source.py)는 결과 계산 전에 연구 커밋 `3b946f4356c76207daac274cf62d3389f53d53d8`에 저장했다. 고정시각은2026-10-19 20:50:57 UTC(10월20일05:50:57 KST).
- 원전3파일111,243바이트의 URL·길이·SHA256을 전부 확인했다. 코드 SHA256은 `0b4776a8b841531675485e8e0ea9a48685b237df6452f7a14035b1b2085cf6be`이다. 나머지 파일 SHA는 규약/보고서에 있다.

원전의 시작 함수·외부 import·그림·환경설정은 실행하지 않았다. 검사한 클래스와 합성자료 생성 함수만 해시 검사 후 AST로 읽었다. 계좌·주문·거래소 비공개 API에 접근하지 않았다. 이는 임의 코드를 안전하게 실행하는 범용 샌드박스라는 뜻은 아니다.

## 실제로 실행한 것

실행은2026-10-19 20:53:23–20:53:38 UTC에 완료했다. Python3.12.14, NumPy2.3.5, pandas2.2.3 환경이다. 직접 결함 재현13개 사례와 회귀 검사14개를 실행했고 14개 모두 통과했다. **통과는 결함 재현의 성공이며 전략 합격이 아니다.**

기존 연구 단위 검사31개를 합한45개도 통과했다. 기존 전략의 전체 백테스트를 다시 한 것은 아니다. 외부 원전 자료 경로를 명시하지 않는 일반 테스트 수집에서는 새14개를 건너뛰며, 원전을 자동 다운로드하거나 시작 함수를 실행하지 않는다.

원전은 시세 다운로드 없이 난수 seed42로 365일×24개=8,760개 1시간 OHLCV를 만든다. 감사에서는 생성 기준 시각을2026-03-30 08:54:15.576895로 고정했다. 이는 게시 결과를 만들었던 원래 시각을 확인했다는 뜻이 아니다. 수익이 좋아지는 시각을 탐색하지 않았다. 게시 장부의72건/PF8.50/93.72%와 이번79건/PF7.407/단순합계90.80%는 일치하지 않는다. **게시 숫자의 정확한 복원 또는 실제 시장 백테스트라고 부르지 않는다.**

| 검사 | 실제 결과 | 의미 |
|---|---:|---|
| 합성자료 진입 |79건|실제 BTC 체결 표본 아님|
| 돌파 전 가격 쪽으로 진입 처리 |59건|봉의 후속 고저를 보고 더 유리한 시가를 선택|
| 사용했던 설정 재사용 |9건|진입 반환 전에 pending 신호가 제거되지 않음|
| 초기 손절 위험 폭이0이하인 진입 |1건|손절/목표 위치가 뒤집힐 수 있음|
| 유효한 ADX |0/8,760행|DatetimeIndex와 RangeIndex 정렬 불일치|
| 손절가 접촉에도 종가 미충족 |1,862개 봉-포지션 검사|서로 다른 손절 거래1,862건이라는 뜻 아님|
| 목표가 접촉에도 종가 미충족 |255개 봉-포지션 검사|목표 선도달 확률로 사용할 수 없음|
| BTC·ETH·SOL 짧은 표본 정규화 OHLC 차이 |최대4.44×10⁻¹⁶|같은 난수 경로의 가격 배율 차이, 독립 시장3개 검증 아님|

별도 작은 입력에서는 상승 갭 봉의 실제 범위가109–112인데105.0001 매수로 처리됐고, 하락 갭 봉은88–91인데94.9999 매도로 처리됐다. 기존 매수의 손절95에 대해 다음 봉이88–92에만 거래돼도95에 손절한 것으로 기록됐다. 이 값은 단위 검사용 가상 가격이며 BTC 실제 가격이 아니다. **전체365일 합성 실행에서 범위 밖 진입은0건**이었다. 작은 반례와 전체 실행의 빈도를 혼동하지 않는다.

원전의 centered swing 계산에는 미래 의존성이 있지만 진입·청산 함수에서 해당 열을 읽지 않았다. 따라서 이것만으로 활성 거래 신호의 미래 누출을 입증했다고 하지 않았다. 반면 돌파 조건 확인 후 이전 시가를 선택하는 체결 문제는 직접 재현했다.

## 비용·검증 범위와 탈락 이유

가격 손익 함수에 수수료·슬리피지·실제 펀딩 차감이 없다. README도 비용·독립 평가를 후속 작업으로 명시한다. 합성자료·잘못된 체결 장부에 임의 비용을 붙여 실전 순이익/순손실을 주장하지 않았다.

순기대수익 및 상관 고려95% 하한, 순PF, 동일 신호의 비용 두 배, 초기R 기준 낙폭, TP1 Wilson 하한, 5분 내 실제 긴급 경고, LBank 수동 체결 지연은 **미검증(null)** 이다. 약0.216건/합성일이라는 빈도도 관측 운영의 빈도 기준을 충족한 근거가 아니다. 소스가 난수로 가격을 생성하므로 자료 누락·생존 편향의 실제 거래소 검증도 성립하지 않는다.

이 원전의 높은 성과표는 대체 알람 채택 근거가 될 수 없다. 단순 버그 수정 후 같은 수익표를 살리는 튜닝 경로로 가지 않는다. 새 전향 관찰은 시작하지 않았고 운영 코드·정책·배포는 변경하지 않았다.

## 재현과 보존

처음부터 복구하는 실행 명령(동일 버전 의존성 필요):

```bash
python research/continuation/audit_external_ema_source.py --sources /tmp/lbank-ema-source --download --output /tmp/lbank-ema-audit
LBANK_EMA_AUDIT_SOURCES=/tmp/lbank-ema-source python -m unittest discover -s research/continuation -p test_external_ema_source_audit.py -v
python research/continuation/verify_external_ema_outputs.py research/continuation/results/external_ema_source_20261020
```

첫 명령은 원전의 고정 URL·SHA가 맞지 않으면 중단한다. 원전 삭제·접근 불가 시 다른 버전으로 자동 대체하지 않는다.

- [실제 계산 보고서](../research/continuation/results/external_ema_source_20261020/report.json)
- [진입 진단 장부](../research/continuation/results/external_ema_source_20261020/source_entry_diagnostics.csv), [합성 거래 장부](../research/continuation/results/external_ema_source_20261020/source_synthetic_trades.csv)
- [압축 자산곡선](../research/continuation/results/external_ema_source_20261020/source_synthetic_equity.csv.gz), [72시간 합성 표본](../research/continuation/results/external_ema_source_20261020/synthetic_72h_sample.csv)
- [보존·테스트 정보](../research/continuation/results/external_ema_source_20261020/delivery.json), [독립 장부 대조](../research/continuation/results/external_ema_source_20261020/independent_audit.json)

독립 대조 코드는 표준 라이브러리만으로 압축 전4개 자료의 SHA·길이와 거래 손익 합계·PF·진입 진단 개수를 다시 계산한다. 재현용 난수 경로·코드·검사만 검증하며 시장 수익성을 인증하지 않는다. 임시 로컬 파일이 유지될 필요가 없다.

다음 단계 및 추가 원전 선별은 [상태](../research/continuation/state.json)와 [이번 원전 검토 기록](../research/continuation/source_screening_20261020_0558.json)에 보존한다.
