# 실제 source qualification — 유한 획득 조사 종료

**결론: 외부 명세·원문이 없어 현재 허용된 자율 범위에서 차단됐다.** 새로 사용 가능한 qualified candle/미사용 비교 표본은 0이다. 기술 구현 완료, 필수 actual P3 비교 미실행, 수익성 미입증을 분리한다. 이 결과는 공식 API 자체가 존재하지 않는다는 증명이 아니다.

## 이번에 실제로 추가 확인한 것

- LBank 공식 support API 분류와 exchange가 가리킨 Python SDK를 확인했다. SDK recursive tree `538d465fe29c7266d9076158bb2742d7b1178f15`와 market/websocket 소스는 spot 경로와 일반 message 전송을 제공하지만 확인한 범위에 futures candle schema/final/volume 정의가 없다. README의 Contract Call 제목은 선물 candle API를 보증하지 않는다.
- LBank 공식 TradingView 연동 안내에서 `BTCUSDT.P` 배포 경로를 발견하고, provider의 symbol 페이지와 정식 CSV export 문서를 확인했다. 이 문서들은 independent native5m/final/volume/원래 수신시각을 증명하지 않는다. 과거 export·같은 chart feed·spot/proxy를 요구 원천으로 대체하지 않았다.
- 지정한 기존 Railway project/environment/service만 **읽기 전용**으로 확인했다. 반환된 bounded 101개 줄 중 사전 의미에 맞는 상태/자료부재 27개 줄을 원문 그대로 `runtime_selected.json`에 보존했다. 요청 limit=100과 실제 반환101을 구분했다. 기존 성공 deployment `a30cf7a6-5adb-4e1d-81e2-63bf07b926e0`, 로그 범위 2026-10-28T19:06:51.849882710Z~19:15:56.558555178Z다.
- 그 범위의 ZB 상태는 `EVIDENCE_INSUFFICIENT`, `independent_5m=false`, `candle_units=false`, funding history0일, verified_closed0, baseline_comparison=null, verified_net=null이다. native closure는 `stream_inferred_not_rest_verified`이고 source는 1m 집계5m다. 준비 봉 수와 장부 input 증가가 실제 source qualification을 해결한 것으로 처리하지 않았다. status에 paper outbound/orders=false가 있으며 이번 읽기는 이 스위치를 변경하지 않았다.

원문 로그의 provider timestamp는 exchange wire receipt timestamp가 아니다. 상태 요약·계산된 RSI/ATR/장부 건수에서 원래 raw receipt나 full derived-input lineage를 복원할 수 없다. 조회한 범위 밖의 무오류·완전연속성·sidecar 동작도 주장하지 않는다.

## 필수 자료별 현재 판정

| 필수 자료 | 이번 추가 근거 | 남은 정확한 차단 |
|---|---|---|
| 공식 futures candle final/volume 정의 | contract docs + 공식 SDK/support index | 문서화된 candle 구독/조회·final 규칙·candle volume 단위 없음 |
| native1m와 독립 발행 native5m | 공식 TradingView 배포 안내만 발견 | 독립 발행/계산 provenance 및 같은 구간 raw1m/5m 없음 |
| closed rollover/reconnect/overlap | 최신 runtime의 inferred closure 상태 | 실제 final raw occurrence와 original receive sequence/연속성 없음 |
| raw→snapshot→전체 derived receipt | 기존 importer/매poll port 구현 보존 | 실제 final history/quote/all-input receipts 없음; 상태 요약으로 대체 불가 |
| 경제 결과 | 최신 runtime verified_net=null | settlement funding/실체결 미확인; quote 비용 모형과 구분 |
| 미사용 연속 비교/P4 | 새 qualified 입력0 | 비교 미실행·정식 승격 판단 불가; 수익 입증 없음 |

`RAW5M_INDEPENDENCE_UNVERIFIED`와 `CANDLE_VOLUME_UNIT_UNVERIFIED`를 유지한다. `source_audit.json`은 공식 URL·확인한 source blob·제한을 보존하고, [acquisition_request.md](acquisition_request.md)는 **보내지 않은** 거래소 기술팀 요청 항목과 기존 importer/매poll 포트의 정확한 수신 인수표를 제공한다. 계정·비밀키·개인체결 접근을 새로 요구하지 않는다.

## 다음 실제 실행 조건

공식 공개 futures candle 명세/독립5m 발행 근거를 제공받거나 위 인수표의 raw receipt가 도착하면 기존 source-dependent decoder·derived receipt 연결의 남은 부분만 검토한다. 그 뒤 사전 고정 미사용 연속 비교와 원래 P4 기준으로 진행한다. 현재 `purpose=unseen` 경로는 차단되어 있으며 boolean을 바꾸어 우회하지 않는다. 같은 개발20개를 새 검증자료로 재사용하지 않는다.

관련86/신규26시험과 retained20 restart exact 검증은 이미 완료돼 이번에는 재실행하지 않았다. capture1SL의 기존 모형 −9.35927984656063USDT/P1미진입0, E5 신규진입0 및 기존 미확정null을 바꾸지 않았다. 동일 HTTP403/빈WS probe도 반복하지 않았다. 코드·임계값·생산 환경·발송·paper/ZB·주문·account/secrets/leverage/billing·Upbit/CAP/ZIL·자동화 변경은 없다.

이번 추가 검증은 문서/저장 산출물 hash·JSON·실제 PR22 필수4파일 blob·허용 경로 diff·원격 tree 일치 확인만 수행한다. 새 합성시험이나 재생은 필요한 원천을 대신할 수 없으므로 추가하지 않는다. 실제 실행 명령/UTC/lease 해제 및 artifact hash는 `manifest.json`, `validation.json`과 두 handoff에 보존한다.
