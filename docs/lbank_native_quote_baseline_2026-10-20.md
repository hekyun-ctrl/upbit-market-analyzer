# LBank 공개 호가 한정 관측 — 2026-10-20

## 실제로 완료한 작업

사전 고정한 12회·5초 간격 공개 BTCUSDT 호가 관측을 **2026-10-20 08:35:32–08:36:27 KST**에 실행했다. 공개 시세의 수신·정합성·시각 한계를 확인한 기초 자료이며, 전략 백테스트·수익성 확인·후보 전향 검증·실전 체결 검증이 아니다.

- 원본 관측 시작: `2026-10-19T23:35:32.429269+00:00`
- 종료: `2026-10-19T23:36:27.943679+00:00`; 실제 55.5144초 뒤 종료
- [사전 규약](../research/continuation/native_quote_baseline_protocol_20261020.json) 및 [실행 전 점검](../research/continuation/native_quote_execution_preflight_20261020.json)
- 실행 코드·8개 검사를 첫 요청 전에 [체크포인트 910e888](https://github.com/hekyun-ctrl/upbit-market-analyzer/commit/910e888abba4f525cab3a91cc5a58ef55c4ac526)에 보존했다.
- [공식 Contract API 문서](https://www.lbank.com/docs/contract.html)의 공개 GET `/cfd/openApi/v1/pub/marketOrder`, depth=5·symbol=BTCUSDT를 사용했다. 공개 HTTP 요청만 수행했으며 계좌·주문·알람 전송에는 접근하지 않았다.

## 실제 계산 결과

| 항목 | 이번 관측 결과 |
| --- | --- |
| 요청 / 정상 호가 / 실패 | 12회 / 7회 / 5회 |
| 실패 내용 | 3·5·7·9·11번째 요청이 5초 제한 안에 응답하지 못함 |
| 정상 응답만의 요청→읽기·파싱 완료 시간 | 중앙값 0.5806초, 범위 0.4661–4.0495초 |
| 시간초과를 포함한 전체 소요 시간 | 중앙값 4.0148초, 95백분위 5.0057초 |
| 정상 응답의 원본 / 정규화 호가 중복 | 각각 0건; 각 7건 모두 다름 |
| 종목·호가 정합성 | 정상 7건 모두 BTCUSDT, 양수 가격·수량, 매도 최우선 > 매수 최우선 |
| 호가 스프레드 | 반올림한 중앙값 0.01219 bps |
| 검증된 호가 생성 시각 | 0건; 정상 7건 모두 최신성 미상 |

이는 **이 연구 호스트의 해당 짧은 관측 구간**에서 측정한 요청 소요 시간이다. 시간초과 5건의 HTTP 상태는 미상이며, LBank 전체 장애·운영 알람 지연·사용자 휴대전화 지연의 원인으로 단정하지 않는다. 정상 응답의 HTTP Date는 호가 생성 시각이 아니다. 중복이 없다는 사실도 최신성이나 실제 체결 가능성을 증명하지 않는다. RTT/2를 편도 지연으로 해석하지 않았다.

호가 폭은 수수료·펀딩·미끄러짐을 포함한 거래 비용이 아니며 수익성 근거로 쓰지 않는다. 7개 정상 표본으로 장기 안정성이나 수동 체결 정책의 적합성을 통과 처리하지 않았다.

## 원본·해시·별도 검산

[원본 12행](../research/continuation/results/native_quote_baseline_20261020/raw.jsonl)에 요청/완료 UTC, 단조 시계 소요 시간, 허용 목록 HTTP 헤더, 응답 본문 또는 오류를 남겼다. 응답 원본을 임의 보간·수정하지 않았다.

| 파일 | SHA256 |
| --- | --- |
| raw.jsonl, 17,648 bytes | `86fd3677627b3d876d8df3ea22e927cd7af1c8f4fcb3c79792119a59ffd0d685` |
| report.json | `b60b260fe4442cb4c4771e195e91a5236677f6cc4832ea17c354400796395794` |
| session.json | `9b3a2ac813d49fac170a39dd974c79a2aee6f7293d0e068af5b1730958fd22ce` |
| independent_audit.json | `0d2e9f6c527b71f47139b91562dd86e3bed8dca1b522e87ddad9eb0c53f77b1b` |
| 실제 실행 코드 v1 | `c796027764604ed91bd498d9b29ab278f06146939304a49884b832a78edbe5c6` |
| 고정 규약 | `6e710d540d4396de18067c82f452ebc1665dea157b3929f7fa93cb5ae6eb5e0c` |
| 기존 검토 파서 | `2092f843e793fb03c8d888ec804e652b3adadb7c86e917220a1bc79bbb6a9496` |

[별도 검산 코드](../research/continuation/verify_native_quote_baseline.py)는 원본 바이트·각 본문 SHA·12개 순번·시계 차이·Decimal 호가 폭·오류 수를 다시 계산해 [원래 보고서](../research/continuation/results/native_quote_baseline_20261020/report.json)와 대조했다. 동일 연구자의 다른 산술 구현이며 외부 기관 검증을 의미하지 않는다.

관측 후 오류 처리 검토에서 HTTP200의 success=false가 권한 오류 코드를 가릴 수 있는 경로를 발견했다. 향후 요청은 오류 코드부터 검사하도록 **v2**를 수정하고 9번째 검사를 추가했다. 이번 원본에는 해당 응답이 없었고 다시 관측하지 않았다. 실제 관측은 체크포인트의 v1·사전 8개 검사이며, 현재 v2는 9개 검사가 통과했다. 원본 report/session의 실행 SHA를 현재 코드로 덮어쓰지 않았다. 상세 연결은 [delivery.json](../research/continuation/results/native_quote_baseline_20261020/delivery.json)에 있다.

## 재현 명령

저장된 원본 재검산과 현재 코드의 네트워크 없는 검사는 다음과 같다.

```bash
python -m unittest discover -s research/continuation -p test_native_quote_baseline.py -v
python research/continuation/verify_native_quote_baseline.py research/continuation/results/native_quote_baseline_20261020
```

위 원본을 만든 실제 v1 명령은 다음과 같다. 현재 다시 실행하면 다른 시점의 새 자료이며 역사적 호가를 재현하는 것이 아니다. 기존 출력 경로가 있으면 덮어쓰지 않고 중단한다.

```bash
python research/continuation/capture_native_quote_baseline.py --capture --output research/continuation/results/native_quote_baseline_20261020
```

## 판단과 구체적 다음 단계

이번 한정 관측은 끝났고 계속 실행되는 수집기를 만들지 않았다. 호가 생성 시각 부재와 환경 의존 시간초과가 남아 **native LBank 체결·지연 승격 기준은 미충족**이다. PF·순기대수익/95% 하한·두 배 전체 비용·TP1·R 낙폭·진입 직후 역행·5분 긴급 경고·알람 빈도는 계산하지 않았다. 전략 후보가 없기 때문이다. 해당 미상 항목을 0 또는 통과로 기록하지 않았다.

[추가 원전 선별](../research/continuation/source_screening_20261020_0840.json)에서 두 공개 구현은 저자 자신의 비용 후 우위 부재/미검증 설명 때문에 수익성 후보로 채택하지 않았다. 다른 15분봉 LightGBM 원전은 코드 검토 전 조사 대상으로 남겼다. 문서의 과거 성과표가 현재 모델보다 오래됐고, -20% 손절·봉 종가 기반 청산·전체 비용 누락·TP1 부재 여부를 먼저 대조해야 한다. 이 손절 폭을 사용자 시스템에 도입하는 승인은 없다.

다음 실행은 고정한 외부 소스 `1274eac3f8e02ddce6be7ff3b48887a971932f35`의 계약 코드·모델 메타데이터·원본 재생 검사·표본 장부를 **실행 없이 먼저 읽고**, 현재 위험 한도/TP1/빈도/비용·누출 적합성을 판정한다. 적합성 확인 없이 학습·최적화·실전 실행으로 넘어가지 않는다. 이미 탈락한 소스를 재튜닝하지 않는다.

합격 후보·전향 관측 시작·운영 교체·배포는 없으며 고정 승격 기준도 바꾸지 않았다. 운영 브랜치와 Railway는 이번 작업에서 변경·재시작·재검증하지 않았다.
