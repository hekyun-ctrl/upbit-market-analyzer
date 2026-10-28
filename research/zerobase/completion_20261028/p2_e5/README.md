# E5 초기 반전 연구 구현·32-arm 개발 비교

E5를 더해도 모든48개 대응 arm×시나리오의 최종 알람 계획·시각·가격·후속 결과가 대조군과 같다. 추가·제외 알람0, 공통 확인 시차·진입가 차0이다. E5 자체 최종 진입0이므로 수익·알람 효용 개선을 입증하지 못했다. P2의 연구 구현·개발 비교 체크포인트이며 전체 기능/운영/수익성 완료가 아니다.

## 선언·범위

최초 선언/lease `884da546f96ed88ccb813649af8ff24490521d3b`, 최종 전략 코드 `826c930a3c5efa9165dc902427bbabbf9f1fdf69`. 이미 알려진10개 완료1m의 지지/저항→최근3봉 sweep→첫 방향 회복 완료봉→고정 반대 고저점의 실제 quote 돌파를 사용한다. 미래 피벗·가짜 완료봉·보간 틱을 쓰지 않는다. 원래 extrema·회복선·유효기한과 첫 사후봉 침범을 검사하며 같은 사건 무효화를 지운 뒤 재무장하지 않는다. 원문 level+sweep source의 합성 해시로 사건을 고정한다.

E5만 RSI 방향/5·15분 역추세를 설명 변수로 기록한다. 자료 누락·평탄 시장·late5m 추격·기존 EMA/가격 추격·구조 위험·비용·RR1.5·목표/수량·쿨다운은 유지했다. 새 전략의 stop은 기존 기본5m geometry를 사용하며 축소하지 않았다. E3 조합은 기존 비breakout의 마지막 완료봉 고저점 trigger를 그대로 사용하므로 E5의 첫 회복 trigger와 별도의 조합 제약이다. E1/E3/E4/P1·운영 코드는 원문 그대로다.1h/4h는 참고뿐이다.

기존16개 arm과 각각 E5를 더한16개 arm,3개 비용/명목 이용지연 시나리오다. 기존48개 대조군 전체 창 출력은 정확히 재현됐다. 같은 개발 구간이며 미사용/연속 검증 자료가 아니다. 숫자를 보고 문턱·목표·RR·비용을 변경하거나 승자를 선택하지 않았다.

## E5 후보의 실제 탈락

|시나리오|E5 평가/고유 사건|초기 통과|최종 E5 진입|실제 첫 탈락 세부|
|---|---:|---:|---:|---|
|base_delay0|96/72|2|0|target_outside_observed_range 40, sampled_context 30, native_target_barrier_conflict 24|
|base_delay15|79/60|2|0|target_outside_observed_range 27, sampled_context 29, native_target_barrier_conflict 21|
|adverse_delay0|96/72|0|0|target_outside_observed_range 59, sampled_context 30, native_target_barrier_conflict 7|

기본0초의 통과2건은 모두 실제 global_cooldown에서 막혔다.15초 대기에서는 같은2건 중1건만 watch에 들어갔고, 첫 후속 확인64.408880초에 원래 작은 trigger를 넘지 못했으며120.418500초에는 sweep 유효기한이 끝났다. 다른1건과 E3 조합의2건은 global_cooldown이다. E1 대응 경로는 같은 사건의 재사용이다. E4를 더하면 위치/추격에서 추가 차단되고, P1에서는 구조 공간부족/미확인으로 초기 통과0이다. 관측되지 않은 확인 실패를 만들지 않았다.

기본0초의 E5 후보96회/72사건 중 RSI 불일치/평탄 설명55회/45사건, 강한15m 역추세 설명2회/2사건이다. 이 설명군은 겹치므로 합산하지 않는다. hard context30회는 flat_sampled_market이며,15초는29회다. 설명 자료는4340개 E5 후보/확인 기록 모두 원래 input_hash와 정확히 같은 상태로 복원했고, 실제 실행되지 않은 검사는 원래 not_evaluated로 유지한다. 원문 CSV의4336개 E5 후보 평가·33552개 구조 scan·5824개 설명 gate 평가는 반복 평가/조합이지 독립 거래 수가 아니다.

## 전체16개 E5 대비·3개 시나리오

|시나리오|대조 arm → +E5|알람 수|신규 SL/미확정|신규 전체 모형 순손익|신규 종료거래 낙폭|
|---|---|---:|---:|---:|---:|
|base_delay0|capture → capture_e5|3→3|3/0|-28.0779196|28.0779196|
|base_delay0|capture_e3 → capture_e3_e5|5→5|3/2|null|null|
|base_delay0|structural_p1 → structural_p1_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay0|structural_p1_e3 → structural_p1_e3_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay0|capture_e1 → capture_e1_e5|3→3|3/0|-28.0779196|28.0779196|
|base_delay0|capture_e1_e3 → capture_e1_e3_e5|5→5|3/2|null|null|
|base_delay0|structural_p1_e1 → structural_p1_e1_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay0|structural_p1_e1_e3 → structural_p1_e1_e3_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay0|capture_e4 → capture_e4_e5|1→1|0/1|null|null|
|base_delay0|capture_e3_e4 → capture_e3_e4_e5|2→2|1/1|null|null|
|base_delay0|structural_p1_e4 → structural_p1_e4_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay0|structural_p1_e3_e4 → structural_p1_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay0|capture_e1_e4 → capture_e1_e4_e5|1→1|0/1|null|null|
|base_delay0|capture_e1_e3_e4 → capture_e1_e3_e4_e5|2→2|1/1|null|null|
|base_delay0|structural_p1_e1_e4 → structural_p1_e1_e4_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay0|structural_p1_e1_e3_e4 → structural_p1_e1_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|capture → capture_e5|2→2|2/0|-18.7186398|18.7186398|
|base_delay15|capture_e3 → capture_e3_e5|4→4|2/2|null|null|
|base_delay15|structural_p1 → structural_p1_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|structural_p1_e3 → structural_p1_e3_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|capture_e1 → capture_e1_e5|2→2|2/0|-18.7186398|18.7186398|
|base_delay15|capture_e1_e3 → capture_e1_e3_e5|4→4|2/2|null|null|
|base_delay15|structural_p1_e1 → structural_p1_e1_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|structural_p1_e1_e3 → structural_p1_e1_e3_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|capture_e4 → capture_e4_e5|1→1|0/1|null|null|
|base_delay15|capture_e3_e4 → capture_e3_e4_e5|2→2|1/1|null|null|
|base_delay15|structural_p1_e4 → structural_p1_e4_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|structural_p1_e3_e4 → structural_p1_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|capture_e1_e4 → capture_e1_e4_e5|1→1|0/1|null|null|
|base_delay15|capture_e1_e3_e4 → capture_e1_e3_e4_e5|2→2|1/1|null|null|
|base_delay15|structural_p1_e1_e4 → structural_p1_e1_e4_e5|0→0|0/0|0.0000000|0.0000000|
|base_delay15|structural_p1_e1_e3_e4 → structural_p1_e1_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|capture → capture_e5|1→1|1/0|-9.3586427|9.3586427|
|adverse_delay0|capture_e3 → capture_e3_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1 → structural_p1_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1_e3 → structural_p1_e3_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|capture_e1 → capture_e1_e5|1→1|1/0|-9.3586427|9.3586427|
|adverse_delay0|capture_e1_e3 → capture_e1_e3_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1_e1 → structural_p1_e1_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1_e1_e3 → structural_p1_e1_e3_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|capture_e4 → capture_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|capture_e3_e4 → capture_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1_e4 → structural_p1_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1_e3_e4 → structural_p1_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|capture_e1_e4 → capture_e1_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|capture_e1_e3_e4 → capture_e1_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1_e1_e4 → structural_p1_e1_e4_e5|0→0|0/0|0.0000000|0.0000000|
|adverse_delay0|structural_p1_e1_e3_e4 → structural_p1_e1_e3_e4_e5|0→0|0/0|0.0000000|0.0000000|

기본 capture+E5 순손익/종료거래낙폭은 −28.0779196/28.0779196USDT,15초는 −18.7186398/18.7186398이며 기존과 같다. E3/E4가 포함된 기본 비용 조합은 기존 미확정 진입 때문에 전체 순손익/종료낙폭이 null이다. 모든 P1 조합0은 미진입이고 미확정 비율 분모0은 null이다. emergency·전체MTM 낙폭도 자료가 없어 null이다. 42개 비교 거래 행은 반복 시나리오/조합이며 독립 거래42건이 아니다. 공통 진입42개 모두 동일 최초 관측 ATR을 사용한 가격차0이고 확인 lead0이다.

## 비용·자료와 산출물

기존/불리 비용: 수수료6/8bp, stop slip2/5bp, 가정 funding1/3bp. 실제 funding0일, 실체결/스프레드/수신·Telegram·수동 실행 지연은 미검증이다. 실제 received/known_at는 null이며 명목0/15초를 실수신으로 부르지 않는다. 같은 거래의 수동30/60초·5/15/30/60분 후속 가정은 trade_cards.json에 있고 독립 거래로 더하지 않는다. 기존 절반/절반 고정 청산 모형과6시간 후속/공백 미확정을 유지한다.

`RAW5M_INDEPENDENCE_UNVERIFIED`와 `CANDLE_VOLUME_UNIT_UNVERIFIED`는 그대로다. 과거6개 forming chart frame은 같은feed 자료이고 독립5m·최종봉·거래량 단위/연속성/펀딩을 증명하지 못한다. 동일 HTTP/WS probe·P0 15개·legacy467개는 반복하지 않았다.

`trade_cards.json`, `trades.csv`, `opportunities.csv`, `gate_decisions.csv.gz`: 전체 거래·후보·실행 gate/실제 사유. `event_accounting.json`, `candidate_selection_accounting.json`: 공통/추가/제외 및 실제 선택·peer·watch 사실. `explanatory_variables.json`, `e5_route_accounting.json`: 해시 일치 설명 자료·방향/역추세와 단계 분모. `fixed_plan_attribution.json`: 최초 관측 S/T 고정 시간 귀속. `manifest.json`, `report_manifest.json`, `validation.json`: 선언·코드·입력·출력·압축해제 SHA256, 실제 명령/UTC/의존성.

## 시험·시도와 미완료

초기 관련60개, 수정 후 E5 최종22개가 통과했다. 중복 제외 고유66개/신규22개다. 첫 비교는 결과를 보기 전에 코드 검토로 발견한 우선순위/사건 구분 문제 때문에 중단(exit130)했다. 원래 breakout 우선순위와 서로 다른 sweep의 ID를 고치고, preempt된 readiness를 null로 초기화했다. 파라미터/성과 문턱을 바꾼 재시도가 아니다. attempts.json과 first_attempt.log에 남겼으며, 수정본 완전 비교는1회다. 사용량 중단 후 재개 시 비교·보고서가 모두 이미 완료됐음을 확인했다. 업로드가 끝나지 않아 전체 결과 저장만 진행했으며 두 계산을 반복하지 않았다.

```bash
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python -m unittest test_lbank_early_reversal_experiment -q
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/compare_early_reversal_experiment.py
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/render_early_reversal_report.py
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/validate_early_reversal_experiment.py
```

P2 독립 E1~E5 및 필요한 조합은 연구 구현/개발 비교까지다. 실패 격리 live EvidenceLedger migration/restart/outage/보존 연결, P3 실제 연속 수신/미사용 자료 비교, P4 정식 판정과 P5 승인된 적용은 미완료다. 기존 수익 기준을 낮추거나 새100paper/30일 선행 조건을 추가하지 않았다. 발송·주문·배포·운영 병합·Railway/Upbit·CAP·ZIL 변경은 없다.

검증/저장 준비 UTC: 2026-10-28T12:35:43.636477+00:00
