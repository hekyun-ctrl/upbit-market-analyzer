# 새 공개 원전 2건: 실제 자료·장부·시점 및 손익 계약 검사

2026-10-20 KST. **두 공개 배포본 모두 대체 알람의 채택 근거로 승인하지 않는다.** 하나는 예제 출력만 있으며, 다른 하나는 원본 장부/자료 출처와 시간·현금·종료 거래 집계 계약이 확인되지 않았다. 이번에는 실제BTC2024년1분기1분봉131,040개로 두 번째 원전의 과거 특징량 변경을 계산했다. 전략의 전체 시장 수익성 백테스트나 모든 추세·상태모형의 실패를 판정한 결과가 아니다.

## 1. 첫 원전은 예제 수치였다

[bhanu-pratap-rana/Trading-strategy-backtest](https://github.com/bhanu-pratap-rana/Trading-strategy-backtest/tree/15391f936e8fa24985392dac7770405f81c5300e)의25Git파일을 확인했다. README의45거래·28승·PF1.85·+12.5%는 **Example Output**이며 공개BTC 원본 가격/종료 거래 장부·체크섬은 없다. 결과를 생성할 수 있는 코드 경로를 이미 생성된 실측 장부로 세지 않았다.

주 실행 경로는 인증 Delta 클라이언트를 초기화한다. 이 경로, 계좌·주문·인증정보는 사용하지 않았다. 공개 문서/설정의 인증정보를 복사하거나 재사용하지 않고 [파일명과 해시만의 조사 기록](../research/continuation/results/bhanu_source_20261020_preflight.json)을 보존했다. 원전의 예제45건을 실제 알람 빈도/승률로 검증한 것처럼 보고하지 않는다.

공개 코드는 승인된 신호 사이의 최소 간격, 지표 확인 개수/전체 개수 비율을 사용한다. 그 비율은 보정된 성공 확률이 아니다. 과거 봉 종가 진입·2ATR 손절·고정4%목표·종료 자료 청산 경로를 제공하지만, 전체 비용·수동 체결 및 현재 위험 한도 계약을 입증하지 못한다. 기본 규칙을 임의로 바꾼 새로운 전략을 만들거나 인증 클라이언트부터 실행하지 않았다.

## 2. 두 번째 원전의 고정 버전과 저자 결과

[pymammadov/bottradepm](https://github.com/pymammadov/bottradepm/tree/dcd1ac3e2a32204c2943bff000644b6f95486aa5)의39Git파일을 조사했다. 코드의 원전 규칙은4시간 돌파/되돌림, EMA/ATR/RSI, 전일 상승 조건, 분할 목표·보호/추적·월별 제약이다. 공개 자료 없이 임의로 특징량을 만들거나60매개변수 탐색을 실행하지 않았다.

실제 저장된 `outputs/report.json`의 저자 최종OOS는+2.3827%, 진입1건, 강제청산1건, 승률100%, PF는NaN이다. 학습5건·검증1건도 있다. **양수 원전 수치를 보존하되 독립적으로 재현한 수익으로 인정하지 않는다.** 원본 가격·개별 종료 거래 장부·원자료SHA·OOS전체 날짜/매개변수 생성 경로가 공개 트리에 없으며, `data_source=best_oos`라는 단계 이름은 거래소/합성 여부의 증명이 아니다. 이를 합성이라고 확정하지도 않았다. 원전 테스트에 합성 단조 상승 자료가 있다는 사실은 저장된 저자 결과의 입력을 확정하지 못한다.

저자 원본JSON3개는 NaN을 포함한 원본 바이트 그대로 `.json.txt`에 보존했고, 우리 엄격JSON의 미확인 값은null이다. 분리된2배수수료/2.5배슬립 스트레스가 있다고 해서 **펀딩까지 포함한 전체 비용2배**를 통과했다고 하지 않는다. 부분 익절 사건별 손익과 종료 포지션 성과도 구분한다.

## 3. 결과 전 고정·실행

[최초 규약·검토 코드 체크포인트](https://github.com/hekyun-ctrl/upbit-market-analyzer/commit/e63fe59faf90ddbbc74ae94c36d67c3b6f36944c)를 실제 가격의 특징량 계산 전에 저장했다. 규약 시각은2026-10-20 14:18:23KST다. 저자 요약 수치를 이미 읽은 코드/자료 감사이며 수익성 맹검 사전등록이 아니다.

첫 실행은 공식CSV의 헤더를 행으로 세어 자료 수 일치 검사에서 중단됐다. 이때 합성 계약 검사는 실행됐지만 실제 가격 특징량 결과는 나오지 않았다. [헤더 인식만 고친 체크포인트](https://github.com/hekyun-ctrl/upbit-market-analyzer/commit/673c06e0a75d54cba2bcdf617cca98b9f93e0099)를 남긴 뒤 다시 실행했다.31조회 날짜·분류 기준·가격 규칙은 변경하지 않았다.

원전 패키지·외부 수집·institutional/최적화·실전봇은 import/실행하지 않았다. 검토한 정의21개(AST)만 별도 이름공간에 실행했다. 합성 회귀 검사10개와 별도 코드의125검사를 통과했다. 실제 가격 계산은14:19:26~14:19:27KST에 실행했다. 실제 실행 시작/종료·코드SHA·규약SHA는 [전체 결과](../research/continuation/results/bottradepm_source_20261020/report.json)에 있다.

## 4. 실제BTC 자료로 계산한 시간 정합성

이미 여러 연구에서 관측한 **Binance USD-M BTCUSDT2024년1~3월** 자료다. 원전 저자 자료·Delta·현물·LBank 자료가 아니며 새로운 독립 구간으로 표시하지 않는다.3개 공식ZIP을 기존Git 공시SHA목록과 대조했고, 분봉131,040개·시간봉2,184개·4시간봉546개·일봉91개를 확인했다. 누락/중복/비정상OHLCV0개이며 이 기간에 기존 격리5분 구간은 없다. 다른 기간의 격리를 해제하거나 가격을 보간하지 않았다.

[전체 입력URL·ZIP/CSV SHA](../research/continuation/results/bottradepm_source_20261020/report.json)에 보존했다. 공식ZIP 경로는 `https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip` 형식이며02/03월도 동일하다. 체크섬 비교는 원본 바이트 무결성 검사이지 실제 수동체결 보증이 아니다.

| 사전 고정 대조 | 실제 결과 | 해석 |
|---|---|---|
|1~3월전체 vs3월11일00:00까지의420개 과거4시간봉|과거 변동성 high/low분류26개가 바뀜|원전 전체표본의 변동성60%분위수를 과거 판단에 쓰므로 미래 추가 불변성을 충족하지 못함|
|동일420봉의 전일 상승 조건|변경0개|이 시점 조건은 전일로 이동돼 있어 해당 대조에서 인과성이 유지됨|
|동일420봉의 EMA20/50·ATR14·RSI14|모두동일|미래 의존이 원전 모든 지표에 있다는 주장이 아님|
|시작 후60~90일의 매일 첫4시간 완료시각31개: 당시까지 vs완전한같은날자료|일봉ADX열24개·`d_ema200`열31개 변경|200일EMA 미성숙 시50일EMA로 채운 값이다. 완료되지 않은 당일 일봉 정보를 초기4시간봉에 배치하는 경로를 재현함|
|동일31조회에서 상승/하락 boolean조건|각변경0개|특징량 값 변화가 이31시점의 실제 방향 변경이나 체결 발생을 입증하지 않음|

분류26개 변경을26개 매매 오류/손절이나 전체 성과 왜곡량으로 바꾸지 않는다. 저자 전체 손익·진입별 오류율은 계산하지 않았다. [420행 대조](../research/continuation/results/bottradepm_source_20261020/real_fullsample_quantile_prefix.csv), [31개 당일 조회](../research/continuation/results/bottradepm_source_20261020/real_intraday_prefix.csv)를 모두 보존했다. 유리한 조회만 골라 보고하지 않았다.

## 5. 합성 현금·집계 계약의 실제 재현

이 절은 시장 전략/신호가 아닌 **고정 진입을 넣은 합성 단위 장부**다. 원전 청산·수수료 코드만 실행하고 특징량/신호 공급부는 진입1회인 통제 입력으로 바꿨다. 원전의 위험 수량 계산은 유지하되 합성 위험액을1수량이 되도록 작게 정했다. 사용자 위험·증거금·운영은 바꾸지 않았다.

- 진입100.02·1수량 →0.4수량 목표102.159564 →0.6수량 손절98.200356이다. 실제 양쪽 명목 수수료를 한 번씩 적용한 순현금은 **−0.315882415680**이며 종료 포지션은1개·수익 포지션0개다. 원전 통계는 익절/손절 사건2개를 따로 세어 **승률50%**를 표시한다. 이 값은TP1 선도달률과도 다르다.
- 원전 장부현금은 **−0.355890415680**다. 진입 수수료를 먼저 차감하고 각 청산에서 다시 원진입 명목분을 차감해 **0.040008**이 추가 차감된다. 이 결함은 해당단위에서 수익을 부풀리는 것이 아니라 손익을 낮추는 방향이다. 진입 이벤트까지 합치면 원전 자체 잔고와는 일치한다.
- 숏에도 `_entry_fill=가격×1.0002`, `_exit_fill=가격×0.9998`를 적용한다.100기준 숏은100.02에 팔고99.98에 사는 유리한 슬립이다. 실제 불리한 숏 슬립은 그 반대다. 시장에서 이 경로가 몇 번 발생했는지는 미계산이다.
- 자료1행만 주면 남은 포지션을강제청산1건으로 처리한다. 보고 잔고9999.839992와 청산 전 equity마지막9999.939992가 달라 종료현금이 최종 경로에 반영되지 않는다. 이 합성1행의DD0을 실제 무위험으로 해석하지 않는다.
- 펀딩 정산 입력/현금흐름이 없으며 손절 갭도 기준선 체결 가정을 사용한다. 실제 선물의 전체비용·갭·native지연 계약을 갖췄다고 표시하지 않는다.

[합성 분할 장부](../research/continuation/results/bottradepm_source_20261020/synthetic_partial/trades.csv), [종료 장부](../research/continuation/results/bottradepm_source_20261020/synthetic_tail/trades.csv), [별도 Decimal·실제 봉 재검산125개](../research/continuation/results/bottradepm_source_20261020/independent_audit.json)를 저장했다. 별도 검산은 원전 AST를 실행하지 않고 ZIP을 다시 읽어420개 분류와31개 일봉 대체EMA를 재계산하고 현금을 대조했다. 제3자 성과 인증이 아니다.

## 6. 판단과 후속

실제순기대수익95%하한·PF·전체비용2배·낙폭R·TP1 Wilson·실제5분내 긴급경고·일별 기회·native호가/체결/지연은null/미검증이다. 종료1건과미공개장부를 전향30일/100건으로 바꾸지 않는다. 이 공개 버전은제외하며, 결함만 고치거나 매개변수를 재최적화해 실전으로 승격하지 않는다. 모든 상태모형/추세 전략의 무효라는 일반화도 하지 않는다.

[다음 원전 선별](../research/continuation/source_screening_20261020_1423.json)은새 논문의원전 코드·비용표·원자료 부재를 구분해 저장한다. 다음단계는저자BTC 전체표와 비용식·세션경계·당시정보·원자료 공식출처를 대조하는 유한 검사다. 원문자료가없으면다른거래소를저자자료로 이름만바꾸지않고 장애로남긴다. 같은평가/같은버전은반복하지않는다.

[운영 연속성](../research/continuation/results/operational_continuity_20261020_1413.json)은기존862339d/배포181eb91b SUCCESS·원본준비·새봉심사가유지됨만확인했다. 연구로재시작/재배포하지않았고 새수익성대체전략의운영교체는아직없다.

```bash
git clone https://github.com/pymammadov/bottradepm /path/to/source
git -C /path/to/source checkout --detach dcd1ac3e2a32204c2943bff000644b6f95486aa5
BOTTRADEPM_SOURCE=/path/to/source python -m unittest discover -s research/continuation -p test_bottradepm_source.py -v
python research/continuation/audit_bottradepm_source.py --repo /path/to/source --folder /path/to/official-cache --manifest research/long_history/results/source_manifest_2024_2026_1m.json --output /new/results
python research/continuation/check_bottradepm_artifacts.py --repo /path/to/source --folder /path/to/official-cache --results /new/results
```

공식캐시는 위Git manifest의URL에서월별원본을복구하고SHA를대조한다. 임시로컬파일의존속이나실행사이AI/연구수집기의연속작동을가정하지않는다.
