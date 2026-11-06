# P2 E3 단독 확인 실험 — 개발 자료 진단

**E3 연구 코드와 4개 arm 비교를 완료했다. P2 전체·알람 개선·수익성 입증은 미완료다.** 실험 선언은 결과 실행 전에 `d2859fa75c8f7c72c828b7c4250f5d5feb2e49de`에 저장했다. E1/E4/E5와 live ledger 연결은 이 체크포인트에서 구현하지 않았다. P1 목표·비용·RR 1.5는 변경하지 않았다.

## 변경과 분리

E3는 고정 30초 대기를 이미 관측한 구조의 준비 여부로 바꾼다. breakout은 최초 level, 다른 경로는 발견 시 완료 1분봉의 진행 방향 극값을 고정하고 실제 quote가 이를 넘을 때 기존 확인 검사를 모두 실행한다. 후보 때 이미 모두 충족되면 같은 관측에서 두 번째 검사를 실행할 수 있다. 이는 별도 시장 관측 두 개가 아니며 독립 확인 근거라고 부르지 않는다.

`confirm_watch`의 keyword-only `minimum_confirmation_seconds` 기본값은30이다. E3 연구 어댑터만0을 전달하며 **실제 age를30초로 위조하지 않는다.** 운영 호출에는 새 인자를 전달하거나 활성화하는 경로가 없다. 기존 구조·RSI·맥락·추격·비용·위험·TTL·쿨다운은 유지한다. discovery 후 첫 완료봉부터 원래 stop 침범을 검사하고 침범·잘못된 자료는 watch를 끝낸다. stop 침범 사건 ID는 개발 창 내에서 기억해 이미 계산한 proposal과 이후 회복 quote가 같은 사건을 재무장하지 못한다. 새 구조 사건 ID만 다시 평가할 수 있다. 첫 사후 봉은 discovery 이전 부분의 접촉도 구별할 수 없어 보수적으로 무효화한다. 현재가로 완료봉을 만들거나 미관측 tick을 보간하지 않는다.

기존 `capture`↔`capture_e3`에서 E3만 바꾸고, 별도 `structural_p1`↔`structural_p1_e3`에서는 P1 목표를 고정한다. 정책별 상태·쿨다운은 독립이다. P1 장벽 차단을 느슨하게 해 알람을 복원하지 않았다. 1h/4h는 참고다.

## 같은 자료 결과

이전부터 여러 번 사용한 신호 주변 관측1,068개다. 미사용 연속 평가가 아니며 실제 수신시각은 null이다. 명목0/15초 가정과 불리한 비용은 동일 사건의 재사용 시나리오다. 모든 scenario의 capture/P1 **창별 전체 경제 출력**이 앞선 고정 P1 비교와 정확히 같다. 장부 ID·코드 provenance만 제외했다.

| 시나리오 | capture→E3 알람 | 공통/제외/추가 | E3 SL/미확정 | E3 전체 모형 순손익·종료낙폭 |
|---|---:|---:|---:|---|
| 기존 비용·명목0초 |3→5|2/1/3|3/2|null / null|
| 기존 비용·명목15초 |2→4|1/1/3|2/2|null / null|
| 불리 비용·명목0초 |1→0|0/1/0|0/0|0 / 0 (미진입)|

기본0초 E3 해결된3건만의 합은 −28.0775826523USDT이나 전체 순손익이 아니다. 미확정 비율은40%, 15초50%다. 기존 capture는 기본0초3건 모두 SL/−28.0779196249USDT/종료낙폭28.0779196249, 15초2건 모두 SL/−18.7186397784다. 불리 비용 capture1건은 SL/−9.3586427252다. E3 기본0초5건 중4건·15초4건 모두 발견 관측과 같은 시각에 확인됐다.

P1 및 P1+E3는 세 시나리오 모두0→0이다. 실제 확인 평가도0회이므로 E3 효과를 식별할 수 없다. 이들의 순손익·종료낙폭0은 미진입이며 미확정 비율은 분모0으로 null이다. 모든 arm의 emergency 수·전체 MTM 최대낙폭은 null이다. 알람 없는 실제 시간과 유효 시간당 빈도는 연속 자료 부재로 계산하지 않는다.

기본0초 capture/E3 공통 사건2개의 확인 시차는0초와62.180124초다. 15초는 같은 숏 사건의62.180124초를 다시 관측한다. 실제 수신·Telegram·체결 지연을 단축한 결과가 아니다. 첫 후보 시각은 공통 사건에서 모두 같고 같은 사건의 시나리오를 합산하지 않는다. 새 알람이 늘었지만 SL·미확정 결과이므로 정확도·수익·진입 품질 개선으로 승격하지 않는다.

## 거래별 내용과 시간 효과 분리

`trades.csv`는 공통/제외/추가12개 시나리오 행, `trade_cards.json`은 각 실제 모형 진입과5/15/30/60분·최대6시간 결과 및30/60초 수동 지연 가정을 보존한다. `event_accounting.json`은 단계별 거절과 비교군 확인 평가 유무를 모두 명시한다. 비교군이 후보 심사를 통과했으나 해당 anchor의 확인 평가가 보존 자료에 없으면 `no_confirmation_evaluation_observed`이며 실패 사유를 만들지 않는다. `gate_decisions.csv.gz`는 모든 단계·입력 해시·첫 거절·미실행 검사·현재 watch 점유를 보존한다.

기본0초의 공통 롱은 같은 가격·시각에 확인됐고 SL이다. 공통 숏은82,749.2에62.18초 빨리 확인됐지만 역시 SL이다. 기존82,738 대비 방향부호 진입 악화는−11.2USDT/BTC다. 실제 정책은 target1도82,214.9557→82,242.9193으로 달라졌으므로 작은 손익차를 순수 시간 효과라고 부르지 않는다.

`fixed_plan_attribution.json`은 **가장 먼저 실제 관측에서 확인된 계획**의 S/T를 양쪽 실제 진입 quote에 고정하는 별도 귀속 모형이다. 미래에 만든 목표를 이전 시각으로 가져오지 않는다. 공통 롱 차이는0이다. 공통 숏의 같은 고정 계획에서 늦은 quote는 비용 후RR1.5를 잃어 미진입0, 빠른 quote는 SL −9.3593573857USDT다. 조기 진입이 좋아졌다고 해석할 수 없다. 이는 실제 정책 경로 합계가 아닌 비교용 반사실 모형이다.

기본0초 제외 숏은 기존SL이며 E3는 `e3_frozen_trigger_not_crossed`(다른 시점 RSI 거절 포함)다. 추가3건은 SL1/미확정2다. 그중2건은 기존 비교군의 해당 anchor 확인 평가가 없고, 나머지는 기존 trigger 소실 거절이 기록됐다. 15초 추가3건도SL1/미확정2다. 사후 결과 누락을0으로 만들지 않는다.

## 비용·자료·시험

기존 비용: 각 taker6bp/stop slip2bp/entry당 funding reserve1bp. 불리 비용:8/5/3bp. 기준잔고1871.9017USDT, 위험0.5%, 증거금15%, 레버리지30의 보존 모형이다. 실제 개인 수수료·펀딩·체결·TP/entry 슬리피지는 미검증이다. 수동30/60초는 다음 실제 보존 quote를 사용하고 고정 S/T의RR을 잃으면 no_entry/0, 후속 불명이면 null이다.

독립 raw5m·거래량 단위·완료 스키마·마감/재접속 연속성·실제 funding은 새로 확보하지 않았다. `RAW5M_INDEPENDENCE_UNVERIFIED`, `CANDLE_VOLUME_UNIT_UNVERIFIED`와 funding0일을 유지한다. 같은 chart feed의5m을 독립이라고 하지 않는다. 기존 probe를 반복하지 않았다.

신규19개 포함 관련92개 시험 통과. 초기85개 실행의 유일한 실패는 의도적으로 바뀐 `confirm_watch` source hash guard였다. 기본30초 보존·운영 활성화 호출 없음·실제 위험/quote 검사·실제 age·첫 봉 stop·수신 미래/공백·미진입0/미확정null 시험과 동일 자료 baseline 비교를 확인한 후 명시적 변경 해시만 갱신했다. 초기 실패 원문도 보존한다. P0 기존15개·전체467개·P1 원자료 probe를 반복하지 않았다.

```bash
python -m unittest test_lbank_confirmation_experiment test_lbank_confirmation_cadence test_lbank_breakout_freshness test_lbank_entry_quality test_lbank_entry_refinement test_lbank_entry_location test_lbank_structural_comparison test_lbank_paper_notifications -q
python research/compare_confirmation_experiment.py
python research/render_confirmation_report.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

`manifest.json`은 재생 코드·고정 입력·출력·해제CSV 해시, `report_manifest.json`은 귀속/사건 보고서 코드·입출력 해시다. 원래 P1 manifest는 이전 고정 commit의 증거이며 이 체크포인트의 수정된 monitor 해시로 과거 증거를 덮어쓰지 않았다. 무발송·무주문·배포/운영병합 없음.

다음 코드 작업은 E1 첫 재시험 포착을 독립 arm으로 분리하고 E3와의 조합 효과를 측정하는 것이다. E4/E5·실패 격리 live ledger·P3 연속 실수신 비교·P4 승격 검토·P5는 남아 있다. 자료 확인을 기다리는 동안 코드를 계속 진행할 수 있으며 수익성 기준을 낮추거나 새 기간/거래수 선행 조건을 추가하지 않는다.
