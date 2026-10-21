# CipherEdge 원본 BTC 거래 비용 대조 — 2026-10-21 14:47 KST

최신 state/continuous 문서 복구 후 별개 원전2개로 탐색을 제한했다. [CipherEdge](https://github.com/M-ax-i-codes/cipher-edge/tree/e0b424cf4f734131d4d532513981396ea94df573) 전체README/실제tree/고정ledger·config·metrics·costs·BTC15m·INTEGRITY 확보, portfolio.ts 전체를 읽어 회계 의미 확인. [XGBoost-BTC](https://github.com/usamatariq014/XGBoost-BTC/tree/a178cd130df384432eb406a650169e567086807f) 전체README/10개tree에 원본가격·결과·고정모델 없음. 수익형 모델로 채택하지 않고 여기서 해당 후보 검토 종료(훈련/모델파일/거래소 실행 없음).

## 고정 규칙과 비용
Cipher 공개 버전 e0b424cf, 보고서 backtest_2026-09-23_15-18-41Z/config hash d57f92765ac46fc1 고정. VMC Cipher B 공식과 동일하다는 주장이 아닌 저자 근사 구현: 1h 구조+15m트리거, 고점 sweep/WT약세괴리/과열60+빨간점/거부봉 숏, 저항 돌파 재지지/모멘텀0이하/초록점·곡률상승 롱. 상세기준은 원본 고정config/code에 귀속하고 혼합 새공식으로 만들지 않았다. TP1 1.5R50%/TP2 2.5R/본절이동/720분시간청산/동일봉충돌stop우선. 수수료10bp 각fill/미끄러짐5bp 각fill/펀딩1bp8h 비례 고정 모형이며 실제 LBank 관측이 아니다. 원본 리스크0.5%/notionalcap20%는 우리 운영 승인으로 옮기지 않는다. LLM=false/allowNetwork=false. 모든 타인 전략 실행·최적화·주문은 하지 않았다.

## 실제 계산과 데이터
명령 `python research/continuation/cipher_BTC_20261021_1447.py`. 표준라이브러리로 원래파일 다운로드/전체SHA/모든 원래BTC ledger 행을 Decimal 재합산. 첫 실행 전 원전 버전/기본config·계산 범위를 고정했고 코드 사전 Git커밋은 하지 않았다(코드 저장 후 결과실행이라고 표현하지 않는다).

전체100행−580.722927USDT 모형손익/PF0.40291685. 그중 BTC38행−109.589478/PF0.63927964, fee146.691442/slip73.345753/funding5.362926. stop18/time13/tp2 7. 저자 IS23건−92.114329/OOS15건−17.475149를 모두 보존하며 이 구간을 우리 처음 보는 독립 OOS라고 부르지 않는다. BTC 단독 재실행이 아니라3자산 공통 잔고/사이징에서 발생한 BTC기여도다.

portfolio.ts의 realized는 미끄러짐이 적용된 fill차익−fees−funding이다. 기본 net에서 slippage 비용을 또 빼지 않았다. 전체100행 실제 entry/가중exit/qty/fee/funding 재구성 오차<0.001 확인. 같은 체결경로 추가fee+slip+funding 차감 BTC−334.989599는 조건부 산술 민감도이며 전체비용2배 재실행/정책동일 경로를 보장하지 않는다.

BTC15m8788봉, 중복0, 인접30분 간격43개=15분봉43개 누락(각1). 누락 위치 모두 report에 보존, 보간·갭강제청산 안함. 이것은 거래시뮬레이터가 gap-safe라는 검증이 아니다. 원래ledger eod0, gap청산으로 수익을 만든 주장 없음. 5m/1h전체 및 엔진 재실행/시간인과성/분할TP1경로/첫5분긴급/수동호가/상관95%CI/R낙폭/native latency는 미검증/null. 데이터가 spot이므로 숏·펀딩 또한 합성모형; 실전 체결 성과로 바꾸지 않는다.

6파일SHA와 실제결과: research/continuation/results/cipher_BTC_20261021_1447/report.json, readback blob ba4ecf36e2170e2eac0ab88caf39852f128e0343. 원자료는 원전고정SHA에서 재취득, 같은 파일명으로 명시. 도구/검사 건수는 성능이 아니다.

## 판단·다음 단계
이 기본 공개 버전 비용후 BTC 손실/PF<1/원문공식불명/갭을 근거로 채택 안함. 모든 돌파·VMC 기법이 무효라는 일반화는 안함. 이미 음수인 해당버전을 살리기 위한 파라미터·좋은기간·자산선택·비용축소/재훈련은 하지 않는다. 기존 참고 알람과 위험경고 유지, 새모의후보/배포/승격 없음.

다음은 다음 실행 신규 native BB후보 ID public_techniques_v1:double_bb_5m:-1:1792557000의 04:48:45UTC 이후 사건을 유한 조회하여 새 open/close 있으면 원래계획·모형비용·누락 대조. 없으면 현재 기록 반복 대신 별개 원전2개 이내에서 완결 원문/고정BTC자료/비용후BTC종료기록 세 조건을 실제 tree로 먼저 확인해 한 건 기본규칙 고정 재현으로 이어간다. 이번 두 소스 및 기존탈락목록 반복 금지. Cipher 결과는 보유사이징/적중 개선 근거가 아니므로 사용자 중간 알림 없음.
