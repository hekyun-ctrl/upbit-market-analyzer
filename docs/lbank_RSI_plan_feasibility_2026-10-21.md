# RSI 진입 계획의 비용·목표공간 정합성 — 2026-10-21

기존37후보의 연속사건 결과에서 실제1분확인/context/RSI까지 통과하여 calculate_plan을 호출한20회만 추적했다. 서로 다른 후보는15개이고 실제거래20건이 아니다. 코드35334d22d7d8513e62a632f38f5efbde00afb365를 계산 전 고정했고 원본정책25388b431c9c9d38df6a434670586c65f382d604를 바꾸지 않았다. trace callback은 반환 위치/수치만 읽으며 재실행한 전체기존 RSI결과가 이전 report와 정확히 일치함을 단언했다.

## 실제 실행
```
PYTHONPATH=/workspace/scratch/fc80326ac0b9/research_deps python RSI_plan_attribution.py
python verify_RSI_plan.py
```
ccxt4.5.89를 작업 경로에 설치했다. 원래 RSI_replay 및 prior.py/원본25388모듈은 앞선 고정 파일에서 복구한다. 기존2025년1월1분44640연속봉/93펀딩행 로더·SHA·완료봉 단언 유지. 새 달력 탐색/미래정보를 진입에 추가하지 않았다.

봉원본 https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-01.zip
SHA256 b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e
펀딩원본 https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-01.zip
SHA256 df725010d55458866eb5a47e9ee708c6796c9d7e77b14c38342cea11f381815e
원본 [calculate_plan](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/25388b431c9c9d38df6a434670586c65f382d604/lbank_futures_monitor.py), [RSI후보 정의](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/25388b431c9c9d38df6a434670586c65f382d604/lbank_rsi_divergence.py). 자체 기존연구이며 유명트레이더 원전 완성규칙으로 간주하지 않는다.

## 결과
| 실제 최종 거절 | 호출 수 | 의미 |
|---|---:|---|
| target_outside_observed_range, 원본431행 | 9 | 비용후1.5R 목표가 최근60분 범위1.5배 밖, ATR예외도 불충족 |
| target_barrier_conflict,438행 | 6 | 후보의 기존 장벽이 목표 전에 위치 |
| stop_distance,412행 | 5 | 손절거리0.6817~0.9162%로 기존0.6%상한 초과 |

20회 중 장벽이 진입가격 앞에 있는19회에 한해 장벽가격에서 전량청산된다는 낙관적인 정적 계산을 했다. 현재 계획모형(편도6bp수수료, 손절2bp미끄러짐,10bp가 아닌 **1bp=0.01% 펀딩준비금**)의 장벽까지 비용후R은 최고1.02627R이었다. 원본1.5R 이상인 건은0회. 비용2배에서 최고0.68066R. 나머지1회는 장벽을 이미 지난 가격이라 양의목표공간으로 취급하지 않았다. 장벽에 정확히 체결되는지, 과거장벽이 실제로 강한지, 이후돌파되는지는 증명하지 못한다.

이 값은 실제보유시간/펀딩결제/체결을 재현한 수익률이 아니다. 계획모형 stop분모/target분자에 기존수수료·손절미끄러짐·고정준비금을 적용한 정합성 검사다. 이전진입시뮬레이터의 편도진입미끄러짐/실제펀딩모형과 혼동하지 않는다. Decimal독립검증127개는 위거절식과 비용정적식의 계산확인이며127수익거래가 아니다.

진입을 더 빨리 보내거나 목표조건을 낮추면 해결된다는 근거가 없다. 목표를 장벽 앞으로만 줄이는 변경도 원본1.5R위험계약을 충족하지 못했다. **이 RSI 후보와 현재 손절·비용·목표계약의 조합을 그대로 운영 승격하지 않는다.** RSI기법 전체/미래시장 전체가 무효라는 일반화도 하지 않는다. 손절·증거금·손실·위험경고 기준 유지.

이 자료는 사용된7일 개발구간이다. 새매매/종료성과0, 기대값/95%상관하한/PF/DD/TP1선도달/실제긴급경고/수동체결은 이 검사로 추정불가다. 15후보를 20독립표본으로 부풀리지 않았다. 결과SHA256 6ce8f33b03561b2aeb60d79d21a3001f17a11df92e7e39726d91cb8998d12cff, remote blob b7ba5972ae4d311519c061e4dc8ef856b3d9d756 재조회 일치.

## native 한 사건 종료 연결
06:10:11KST 모의 박스LONG1792530000는 08:10:45KST levels/time_exit, 모형순손익-3.430318932598397 USDT로 종료됐다. 같은id의 warning_exit도 같은 결과이며2거래가 아니다. closed발신API delivered=True08:10:45 확인. runner종료/TP1최초시각/기기수신/전체관측공백·개인수수료/실제펀딩/실제계좌손익은 미확인. 원문로그 native_episode.json 보존. 연구합격 전향후보로 세지 않는다. 새배포/재시작/주문 없음. 최신production원격 HEAD도25388b4로 확인했다.

## 다음 단계
기존4종/RSI의 같은 개발창 재계산·숫자거절감사는 여기서 종료한다. 단일 source-grounded변경을 승격할 근거가 나오지 않았으며, 기준을 풀어 통과시키지 않는다. 이제 상태에 이미 보존된 원전후보 The Recurrent Reinforcement Learning Crypto Agent(DOI10.1109/ACCESS.2022.3166599, UCL원문/2201.04699)의 실제 전체비용·펀딩·원본코드/자료/완료거래와 수동BTCUSDT보호계약을 한정 검토한다. 논문초록/개인수익헤드라인을 우위로 채택하지 않는다. 자료·규칙·비용후거래가 없거나 inverse/자동화전용계약이면 정확한장애를 기록하고 다른 공개원전으로 진행한다. 새모델훈련/파라미터탐색/위험확대 없이 기존원전의 재현가능성부터 확인한다. native후보들의 새종료/검열/누락은 계속연결하고 반복상태메시지를 성능향상으로 보고하지 않는다.
