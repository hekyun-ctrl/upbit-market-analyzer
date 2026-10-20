# 공개 Pullback 원전: 실제 모의 엔진의 진입 보호·청산 검사

2026-10-20. **외부 원전의 체결·보호·장부 계약을 재현 검사했고, 이 공개 버전은 대체 알람 채택 근거에서 제외했다.** 기존 LBank 운영 코드의 결함을 찾아냈다는 뜻이 아니며, 모든 EMA/되돌림 전략을 무효라 하는 것도 아니다. 시장 수익성 백테스트 또는 실전 수익 검증은 수행하지 않았다.

## 고정 원전과 실행

[Silentbilly 원전](https://github.com/Silentbilly/trend-pullback/tree/22271525f93d04188c2791d48e1029a6101d39c3), 규칙 버전 `22271525f93d04188c2791d48e1029a6101d39c3`. 공개36파일에 실제 원본 가격/종료 장부가 없으며, README의 `output/trades.csv`는 출력 예정 경로이지 저장된 성과가 아니다. README의 예시 값보다 실제 설정·코드를 우선했다. 원래 sample은 **ETHUSDT 현물 15분봉**, 34/200/13 EMA, 최대되돌림3봉, ADX22, CHOP 꺼짐, ATR돌파버퍼0.08, ATR손절배수2.4, RR2다. BTC 양수 전략이라고 소개하지 않는다.

[규약](../research/continuation/pullback_contract_protocol_20261020.json)과 [검사 코드](../research/continuation/audit_pullback_contract.py)는 결과 전 [c55bc6c 체크포인트](https://github.com/hekyun-ctrl/upbit-market-analyzer/commit/c55bc6c3d7581698894af582431ac3d981e49a16)에 고정했다. 정적 검토로 수립한 가설을 검사했으므로 맹검 알파 검증이 아니다. 실제 실행은 **2026-10-20 16:13:22 KST**에 완료했다.

검토22개 AST 정의와10파일만 사용했고, 실제 `backtrader==1.9.78.123`의 메모리 내 Cerebro에서 원전 전략 클래스를 실행했다. 추적용 하위 클래스는 원전 판정을 바꾸지 않고 주문·포지션·잔고를 기록했다. 원전 패키지 전체 import, live/broker/notifier, 계좌·주문 연결, 다운로드·최적화·자본배분 스크립트는 실행하지 않았다. 설치한 공개 라이브러리는 별도 임시 경로에 두었고 보고서에 엔진 핵심5파일 SHA256을 저장했다.

## 실제 엔진에서 재현한 결과

다음은 **고정 합성 가격과 강제 첫 신호**를 사용하는 실행 계약 검사다. 실제 거래 또는 전략 승률의 표본이 아니다. 기준 진입100, 계획 위험2, 목표104/손절98, 수량1, 시험자금1000, 편도 수수료0.06%다. 실제 사용자 자금·레버리지·허용손실과 무관하다.

| 검사 | 실제 엔진 결과 | 의미 |
|---|---|---|
| 진입한 봉의 저가90, 손절98 접촉 | 청산0, 롱1 유지 | 체결 알림에서 새로 낸 보호 주문은 진입 봉 내 움직임을 보호하지 못함 |
| 다음 봉이 목표104와 손절98 모두 접촉 | 매도2회, 반대 숏1 잔존 | 원전은 목표·손절을 OCO로 연결하지 않음. 콜백 취소 전에 두 주문 체결 |
| 숏의 목표·손절 동시 접촉 | 매수2회, 반대 롱1 잔존 | 양 방향 모두 재현. 잔존 포지션의 원전 active stop/take는 null |
| 손절만 접촉 | 정상 청산, 순손익−2.1188 | 올바른 손절·수수료 대조군 |
| 목표만 접촉 | 정상 청산, 순손익+3.8776 | 올바른 목표·수수료 대조군 |
| 시가95로 손절98 아래 갭 | 95 청산, 순손익−5.1170 | 불리한 갭 가격이 실제 반영되는 대조군 |
| 시험자금50으로 진입 거절 | Margin 뒤 pending entry와 ref가 남음 | source에서 Margin을 거절 정리 분기에 포함하지 않음 |
| 진입이100→110으로 갭 | stop108, take114 | 계획 가격98을 유지하지 않고 계획 위험2를 체결가에 재고정하는 원문 계약. 이것 자체를 보편적 오류라 하지는 않음 |

동시 접촉 롱에서 원전 종료 장부의 순손익은+3.8776이지만, 추가로 열린 숏의 평가손익·수수료까지 포함한 계정 가치 변화는+1.8188이다. 숏 반례도+3.8824와+1.8212로 달랐다. 원전의 정상 종료 손익 숫자는 대조군에서 맞았지만, 종료 장부만 합쳐 계정 전체 성과로 바꾸면 안 된다. 실제 시장에서 이 결함이 몇 번 발생했는지나 전체 수익 왜곡 규모는 계산하지 않았다. 시험 종료 미청산 포지션은 강제 청산하지 않고 상태를 그대로 남겼다.

원본 분석기의 종료 수량은0, 종료 가격은 진입가100으로 기록됐다. 동시에 PnL은 실제 엔진 값이 남았다. 이를 손익이 모두 0으로 집계됐다고 오해하지 않는다. 종료 거래가 없고 equity가1000→900인 별도 보고 함수 반례에서는 낙폭/순손익을0으로 반환했다. 이는 `closed-only`와 미청산 평가 잔고를 분리해야 하는 또 다른 이유다.

수수료 두 배 대조군의 손절 순손익은−2.2376, 목표는+3.7552였다. **이는 회계 대조일 뿐 수수료·슬립·펀딩 전체 두 배 성과의 검증이 아니다.** 비용을 낮추거나 손절을 넓혀 채택시키지 않았다.

공식 엔진 문서의 [주문 실행](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/), [OCO](https://www.backtrader.com/docu/order-creation-execution/oco/oco/), [bracket](https://www.backtrader.com/docu/order-creation-execution/bracket/bracket/) 설명도 대조했다. 이번 판단의 직접 근거는 보존된 실제 엔진 주문 상태/체결 기록이다. 원전의 OCO를 고쳐 새 전략 성과처럼 재사용하지 않았다.

## 실제 BTC 가격의 인과성 검사와 한계

기존에 사용한 Binance USD-M BTCUSDT 2024년1월 **44,640분**을 원본 ZIP/CSV SHA와 대조하고, 60,000ms 간격·중복/누락0·유한 OHLCV·양수 가격·가격 범위·비음수 거래량을 확인했다. 정확히15개 분봉씩 **2,976개15분봉**으로 집계했다. 원전 ETH 설정을 그대로 적용해 **신호 인과성만 검사**했으며 ETH 현물 복제나 BTC/LBank 수익성으로 바꾸지 않았다.

첫20일1,920봉과 미래11일을 추가한 전체31일을 비교했다. 출력44열 ×1,920봉 = **84,480셀 변경0**이었다. 따라서 이번 대조에서 미래 누출 반례를 찾지는 못했다. 모든 시간·자료에서 미래 누출이 없다는 보증은 아니다.

원전 규칙의 원시 신호는 롱16·숏11, 31일 중17일에 있었다. 원시27/31≈0.871건/일로 이 한 구간에서는 최소 일1건보다 적지만, 이는 실제 수동 진입/종료/기회 빈도가 아니다. 포지션 중복 억제·체결·초기 위험·발신 시점은 미적용이다. 한 달만으로 모든 시기의 빈도나 수익을 일반화하지 않았다. [27개 원시 신호와 지표/계획 위험](../research/continuation/results/pullback_contract_20261020/actual_BTC_signal_diagnostics.csv), [44열 대조](../research/continuation/results/pullback_contract_20261020/actual_prefix_comparison.csv)를 보존했다.

자료 원본:

- [공식 ZIP](https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip), [공식 체크섬 주소](https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip.CHECKSUM). 이번에는 기존 Git에 검증 보존된 manifest와 실제 파일을 대조했다. 새 다운로드 검증이라고 말하지 않는다.
- ZIP SHA256 `21eeac04a76a7a35b10467e5e752fb2f8cff77cdeb57df6b50a23ce8d69bb190`
- CSV SHA256 `ab25f0fee86faa93df62f800adf92b742abdc60982e023159d0ca9faf3a4ba14`
- [기존 원본 manifest](../research/continuation/results/pinbar_transfer_20261020_retained/sources.json), SHA256 `c6468c70aa4aa45f4f6315a6477450e2119c76bda826a1db4ee1b144ec6e5569`

## 추가 시점·품질 검사

원문 loader는 Unix seconds 지원을 문서화하지만, 실제1704067200/1704068100 입력을 ns로 읽어1970년 시각으로 바꿨다. 두 값은 의도한900초가 아닌900ns 차이다. 별도 합성 테스트에서 중복 시각과2시간 공백도 거절하지 않았다. 실제 가격 입력은 위의 별도 엄격 검사를 통과했고 올바른 ms 단위를 썼으므로 이 loader 반례로 이번 실제 가격의 날짜가 틀렸다고 말하지 않는다.

RSI 초기 미성숙2값이100으로 채워지는 동작도 재현했다. 실제 원시 진입에는 EMA200 등 다른 준비 조건이 있어 이것이 실제27신호를 왜곡했다고 주장하지 않는다.

**15개 회귀 검사**를 실제 엔진으로 재실행했고, 별도 표준 라이브러리/Decimal 검산 **44,784회**를 통과했다. 그 대부분은44,639개 분봉 연속성 검사다. 독립 거래 수나44,784개 수익 검증으로 세지 않는다. 독립 회계 최대 절대오차는1e−13이다. 전체 주문/잔고10사례·정상 대조·Git/SHA·실제 자료/원시 신호 수를 [별도 검산](../research/continuation/results/pullback_contract_20261020/independent_verification.json)에 남겼다.

## 미측정 게이트와 다음 단계

원전 코드/설정/자료·보호 계약이 맞지 않아 유효한 시장 성과 장부를 만들지 않았다. 시장 비용 후 순기대와95% 하한, PF, 두 배 전체 비용, 낙폭R, TP1 Wilson, 진입 직후 역행·긴급 경고, 실제 알람 빈도·수동 체결·native LBank 가격/호가/공개 지연은 null이다. 2024년 자료를 처음 보는 순방향 자료로 표시하지 않는다. 과최적화·다중선택을 피하려고 새 설정 탐색·생존 종목 선택·재최적화는 하지 않았다. 합격 후보·전향 관측 시작은 없으며 임시 실행 사이 AI/수집기가 계속 작동한다고 주장하지 않는다.

다음은 **공개 BTC 거래·시간별 PnL·원본 가격/펀딩 파일 경로가 버전 관리되는** `shivanshu1177/funding_harvester` 고정776003e의 기본/OOS 장부를 대조하는 유한 계산이다. 파일 이름이나 README 양수만으로 실제 우위 또는 종료 거래 내역 확보를 확정하지 않는다. 규칙·시점·수수료/슬립/펀딩/날짜/진입·종료 단위를 먼저 대조하고, 기본 공개 경로를 고정해 비용·기회일·불확실성을 계산한다. 81설정 탐색, Kelly 배분·레버리지·위험 확대, 원문 비공개 자료/계좌 접근은 하지 않는다. 저자가 보고한 낮은 빈도·작은 양수와 IS 손실도 함께 남기며, 빈도가 미달이면 이를 고치려 재튜닝하지 않는다. 중복 후보는 기존 제외 목록과 대조했다.

운영862339d/배포181eb91b SUCCESS는 읽기 확인했다. 16:08:15KST에 연속1분326개/5분64개 native_ready, 16:09:05에 기존 정책 새 완료봉 심사·후보0을 확인했다. 최초 필터 조회는 빈 응답이었고, 별도 제한된8행 조회에서 확인했으므로 빈 응답을 장애로 단정하지 않았다. 실제 거래소 공개 시각·수동 체결·발신 전체 지연은 미측정이다. 연구를 저장하기 위해 재시작·배포하거나 기존 위험 경고/손실 상한을 바꾸지 않았다.

## 재현

```bash
git clone https://github.com/Silentbilly/trend-pullback.git source_pullback
git -C source_pullback checkout --detach 22271525f93d04188c2791d48e1029a6101d39c3
python -m pip install backtrader==1.9.78.123 numpy pandas pyyaml
PULLBACK_SOURCE_REPO=source_pullback python -m unittest discover -s research/continuation -p test_pullback_contract.py -v
python research/continuation/audit_pullback_contract.py --repo source_pullback --data-root RAW_CACHE --manifest research/continuation/results/pinbar_transfer_20261020_retained/sources.json --output research/continuation/results/pullback_contract_20261020
python research/continuation/verify_pullback_contract.py --repo source_pullback --data-root RAW_CACHE --output research/continuation/results/pullback_contract_20261020
```

`RAW_CACHE/klines/1m/BTCUSDT-1m-2024-01.zip`를 위 공식 주소에서 복구하고 해시를 확인한다. 원본 파일이 다르면 스크립트가 중단한다. Git 원전 객체를 읽으므로 원전 설치·로그인·서비스 기동은 필요 없다. [보고서](../research/continuation/results/pullback_contract_20261020/report.json), [합성 엔진 전체10사례](../research/continuation/results/pullback_contract_20261020/synthetic_broker_cases.json), [검사 코드](../research/continuation/test_pullback_contract.py), [독립 검산 코드](../research/continuation/verify_pullback_contract.py).
