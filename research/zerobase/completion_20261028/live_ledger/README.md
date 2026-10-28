# Live EvidenceLedger 어댑터 — 구현 체크포인트, 운영 활성화 전

2026-10-28T12:52:48.754373+00:00에 실제 운영/연구/PR21/22/27과 비활성 lease를 확인했다. 시작 PR27은 `1a30b248b2dae9eb16b16b28b756b65b379d16a8`, 단일 lease는 `392ec8edd9360730ee746bf69af6eb415e072432`, 초기 코드 저장은 `436656a696e2c9d9c9f39e128c16f441feec0640`이다. 후속 검토에서 불변 후보 연결과 원문 wire byte 보존을 보완했다. 전체 기능·연속 실수신 검증·수익성·운영 승격 완료가 아니다.

## 실제 변경

`lbank_live_evidence.py`의 선택적 sidecar를 기존 native collector와 capture/location monitor의 관측·후보·확인·만료·알람 자격·고정 계획·기존 알림 결과·신호 후속 관측에 연결했다. `LBANK_LIVE_EVIDENCE_ENABLED` 기본값은 false다. 기본 경로에서는 DB/worker를 만들지 않는다. 이 설정을 운영에서 변경하거나 배포하지 않았다. 별도 `lbank_zb/service.py`/`journal.py`/`engine.py`의 기존 거래 장부·불활성 outbox·자료 차단은 읽어 확인하고 변경하지 않았다. P1/E1/E3/E4/E5 계산과 기존 전략 문턱도 그대로다.

프로듀서는 공개 시장 입력을 JSON snapshot으로 고정하고 `put_nowait`만 사용한다. DB 열기·migration·잠금 대기·쓰기·checkpoint는 daemon worker가 전담한다. queue는 64개, 개별 envelope는 512KiB로 제한한다. 기존 객체 수정은 저장된 snapshot을 바꾸지 못한다. startup 실패·queue 포화·과대 입력·저장 실패는 분석 손실로 기록하며 기존 위험/collector/알림 판단을 기다리게 하지 않는다. producer의 JSON 복사·해시는 입력 크기만큼의 CPU 작업이며 무비용·실시간 처리 보장은 아니다.

## Migration·원자성·재시작

기존 `trade_evidence_v1` payload/ID는 유지하면서 SQLite `user_version`0/1에서2로 additive migration한다. 더 새로운 버전은 변경 전에 거절한다. core EvidenceLedger의 중첩 transaction을 이용해 envelope와 source/input/decision/plan/disabled intent/unresolved outcome을 함께 commit 또는 rollback한다. 기록 도중 disk failure 뒤 재시도해도 부분 계획은 남지 않는다.

후보 원문 snapshot의 정확한 anchor를 candidate record hash로 watch→확인→고정 계획에 전달한다. 확인 시각에 같은 가격의 더 늦은 봉을 다시 골라 같은 사건을 바꾸지 않는다. 후보 기록이 유실되거나 anchor가 없으면 명시적으로 unmatched다. 표시 reference+sent_at과 frozen record의 연결도 DB에 저장해 재시작·표시 번호 재사용을 구분한다. 동일 envelope 재생은 hash로 중복 제거한다.

각 process session은 시작/정상 종료를 기록한다. 닫히지 않은 이전 session이 있으면 `unclean_restart`와 `lost_records:null`을 남긴다. RAM queue의 crash 손실 개수를 추정하거나0으로 만들지 않는다. queue/쓰기의 알려진 손실은 회복 후 `gap`에 개수를 기록한다. 회복 전 다시 중단되면 다음 session의 손실 범위는 불명이다. 이것은 durable outbox 또는 외부 exactly-once 전달 기능이 아니다. 재시작 시 발송을 시도하지 않는다.

## 후보·진입·결과·자료를 구분

후보/거절/확인/알람 자격은 entry/fill이 아니다. 계획을 고정한 뒤의 미관측 결과는 `unresolved`, `net_pnl_model:null`이다. 진입하지 않은 정책의 가짜0손익 거래를 생성하지 않는다. 후속 target/stop/exit 관측과 알림 transport 결과도 실제 계정 청산·체결·완결 손익으로 바꾸지 않는다. 기존 transport는 boolean만 제공하므로 true 또는 모호한 timeout은 `send_ack_unknown`이며 provider message ID/수신 시각을 만들지 않는다. 실제 ID를 가진 별도 입력만 core의 acknowledged 검사를 통과할 수 있다.

native frame은 수신 callback의 local wall clock, 원래 wire bytes/정확한 SHA256, 해석된 frame을 함께 보존한다. 동일 wire frame의 다른 수신은 별도 occurrence다. monitor snapshot을 원문 봉 수신으로 취급하지 않아 snapshot `received_at`, anchor `known_at`, decision `available_at`는 null이다. 실제 실행된 monitor diagnostics를 보존하지만 연구 trace의 전체 per-gate 계측이 연결됐다고 주장하지 않으며 미실행 gate vector는 `not_evaluated`다. raw occurrence→완료봉→정확한 snapshot의 완전한 causal binding과 sealed 연속 replay importer는 다음 작업이다.

`RAW5M_INDEPENDENCE_UNVERIFIED`·`CANDLE_VOLUME_UNIT_UNVERIFIED`는 유지한다. 같은feed 1m/5m forming frame은 독립5m·공식 final·거래량 단위를 증명하지 않는다. 1h/4h는 참고이며 신규 gate를 만들지 않았다.

## 보존 상한과 감사

기본 main SQLite page cap은64MiB, live envelope cap은100,000개다. WAL을 매 envelope 전에/후에 truncate하고 pinned reader로 해제가 안 되면 추가 기록을 중단한다. main+WAL의 검사 시점과 한 transaction 사이에는 WAL이 일시적으로 늘 수 있다. main cap과 한 transaction의 최대 page 수로 인해 main+WAL은 대략 main cap의2배+SQLite/WAL/SHM 부가량까지 허용해야 한다.64MiB를 모든 파일 합계의 엄격한 상한으로 표현하지 않는다. RAM queue와 이미 존재하던 다른 장부/원본 저장소는 별도의 상한이다. 설정 cap을 초과한 기존 DB를 삭제하거나 축소하지 않는다.

한도 도달 시 선택적 분석 기록만 실패하며 미완료 비교·미확정 계획을 삭제하지 않는다. `LiveEvidenceStore.archive(destination, comparison_reference=...)`는 새 경로로 전체 SQLite backup을 만들고 integrity/foreign-key 검사를 거쳐 archive SHA256·byte·건수·comparison reference를 별도 manifest에 남긴다. 이 reference는 완료 인증을 자동 생성하지 않는다. archive는 별도 저장 용량을 사용한다. 자동 prune/rotation/deletion은 없다. 실제 비교 완료·보존 정책 검토와 검증된 archive를 확인한 뒤의 운영 보존/회전 절차는 미실행이며, 한도 도달 후에는 검토 없이 새로 기록할 공간을 만들지 않는다. 이렇게 보존 실패를 숨기면서 오래된 근거를 지우지 않는다.

## 검증과 전체 증거

관련137개 suite가 통과했고, 불변 후보 연결 검토 뒤11개(신규2개 포함), wire byte 보존 뒤12개(신규1개 포함)가 통과했다. 중복 제외140개/신규25개다. 이 숫자는 여러 관련 실행의 합집합이며140개를 한 번의 마지막 suite에서 실행했다고 주장하지 않는다. core transaction 변경 때문에 기존 P0 15개와 관련 native/risk/timing/health 회귀를 다시 확인했다. 완료된 P0/P1/E1~E5 전체 replay·기존 HTTP/WS probe는 반복하지 않았다.

- storage worker를 의도적으로 막고 queue를 포화시킨 동안 실제 `NativeCandleStore.ingest`와 `SignalRiskTracker.check`가 진행됐다. 합성 위험 event1개와 native row1개, 약0.0101초를 `blocked_writer_risk_collector.json`에 보존했다. 이는 해당 fixture의 격리 증거이며 운영 지연 보장이 아니다.
- 실제 보존 raw frame6개의 원래 byte/hash/수신 시각을 다시 가져와 저장했다.4개1m/2개5m이고 새 네트워크 요청·독립5m·검증된 완료봉은0이다. `retained_raw_receipts.json`은 기존 실제 수신 증거이지 새 연속 수집이 아니다.
- 사전 선택 규칙(첫6개 available 관측+기존 저장 후보 시각6개)으로 실제 개발 관측12개를 확인했다.6개는 qualified candidate다. healthy storage와 강제 SQLite abort 뒤에도 입력과 정책 출력 hash12쌍이 같았다. `retained_policy_integration.json`에 정확한 원문 quote 시각·입력 hash·출력 hash·실패 조건이 있다. 새12개 독립 거래/수익/holdout이 아니다.
- migration/미래 버전 거절·atomic rollback·restart dedup·늦은 입력 거절·ACK 불명/불변·reference 재사용·상한·pinned WAL·검증 archive·과대 입력·outage 회복·원문 wire hash 무결성을 시험했다.

artifact 저장용 추가 실행에서 frame의 d가 list/dict 두 형태라는 test-report 코드 오류1개가 발생했다. runtime 구현 실패가 아니며 로그를 삭제하지 않았다. list/dict 추출을 고치고 실패한 그 test만 재실행해 통과했으며, 이후 wire byte 보존 변경의 관련 실행도 통과했다. `artifact_capture_attempt.txt`와 `artifact_capture_resolution.txt`를 함께 보존했다. manifest에 실제 명령·해시·검증 범위를 기록했다.

## 남은 작업과 권한 범위

라이브 실행 코드 연결과 실패 격리의 로컬 검증까지다. 실제 배포/활성화·상시 동작·원문과 완료봉/snapshot 간 causal coverage·연속 true-receipt 자료·unseen 정책 비교를 입증하지 않았다. P3 sealed 연속 importer/재시작/공백 audit, P4 qualified review, P5 명시적으로 승인된 적용은 남아 있다. 운영 보존 archive/rotation 검토도 실행하지 않았다. 자료 단위·독립5m·공식 closure·funding/체결/스프레드 부족은 그대로다.

수수료6/8bp·stop slip2/5bp·가정 funding1/3bp, 실제 funding0일과 수동30/60초·명목0/15초 변형의 비독립성을 바꾸지 않았다. 수익 문턱을 낮추거나100paper/30일 선행조건을 추가하지 않았다. 발송·주문·배포·운영 병합·Railway/Upbit·CAP·ZIL 변경·추가 작업자·모델 대체·자동화 생성은 없다. 이전 E5의32 arms·3 scenarios·48개 대조 출력과48개 최종 출력 동등성 결과도 변경하지 않았다.
