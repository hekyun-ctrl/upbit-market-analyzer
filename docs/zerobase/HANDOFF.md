> 2026-10-21 수정: R1–R6는 REVIEW_FIXES_20261021.md에 따라 수정·38시험 통과. 아래는 첫 구현 당시 기록이며 최신 수치·동작은 수정 인수인계를 우선한다. 전체 ENGINEERING_READY/수익성 검증은 아직 아님.

# ZB-1.0 첫 구현 묶음

사용자 최신 지시: 기존 연구 중단 유지, 첨부 설계서 기준 구현. 기존 자동 연구를 재개하지 않았다. 기준 코드는 `25388b431c9c9d38df6a434670586c65f382d604`, 별도 브랜치 `feature/lbank-zerobase-zb1-20261021`. 기존 운영 진입·청산·발송 파일은 수정하지 않았다. 주문·계좌·LLM·발송 네트워크 호출 없음.

## 실행

저장소 루트에서 Python 3.10+ 표준 라이브러리만 필요하다. SQLite writer lock은 Linux fcntl 사용.

```bash
python -m unittest discover -s tests/zerobase -v
python research/zerobase/inventory.py
python -m lbank_zb.replay --input normalized_native.jsonl --output result.json
# 명시적인 연구 비용 가정을 사용하는 경우에만:
python -m lbank_zb.replay --input normalized_native.jsonl --scenario assumptions.json --latency 120 --output result.json
```

입력은 `Bar` 필드의 JSONL: `start/period/o/h/l/c/v/received/closed/exchange/market_type/symbol/settlement_asset/price_basis/volume_unit/source/revision`. 가격·수량은 문자열 Decimal. 시각은 UTC 초. 실제 도착 순서, 동시도착1m→5m. 이 도구는 원본 공급자를 자동 연결하지 않는다. 미확인 비용은 null, 외부 알림은 기본 비활성이다. `Costs` 필드 전체가 시나리오 JSON이며 `status=ASSUMED`, `version`에 출처를 명시한다. VERIFIED라고 파일에 적는 것만으로 순손익 검증이 되지 않도록 verified_net은 연결 완성 전 항상 null이다.

## 구현 대응

| 설계 경계 | 현재 산출물 | 상태 |
|---|---|---|
| 자료 계약/봉/상품/호가 | core.Bar, aggregate, adapter | 정규화·거부·호가 수량 계산 구현. 실시간 공급원 연결 미완료 |
| 지표/확정pivot/15m장세 | core.features/pivots/regime | 결정적 구현·고정 예제 시험 |
| A/B/C | core.detect | 독립 후보·양방향·B고정범위/3봉만료 구현 |
| 비용·목표·수량/충돌 | core.plan/select | Decimal·보수반올림·범위·RR·비용·중복·상충 구현 |
| 보유관리 | core.Position | 원래F/S/I고정·손절우선·25%부분·추적·6/24h검토 구현 |
| 장부/재생 | engine/replay | 입력시계·단일포지션·위험중지·재생·명시비용 시나리오 구현 |
| 기록/outbox | store | SQLite WAL·원자저장·재시작·writer lock·중복·정정분리·만료 구현. 실제 발송기 미연결 |
| 설명지표 | RSI/divergence 기록 | v1 진입에 영향 없음. BTC.D/USDT.D/USDTUSD는 UNKNOWN, 공급원 미연결 |
| 평가 | evaluation | 고정seed 3일블록2,000회/쌍체함수. 완전한 성과표·유리/불리봉내범위·baseline연결 미완료 |

## 실제 실행 결과

31개 동작 시험 통과. 상품 혼용, 미완료봉, pivot known_at, 봉 도착 기한, 갭·중간진입·손절우선, 새손절 소급금지, 부분청산/펀딩 부호·정확 현금, 비용미확인, 충돌, 재진입 방지, 거래 전 손절, SQLite rollback/중복/재시작, outbox 만료/응답유실, 위험중지중손절 등을 시험했다. 테스트는 합성 고정 입력이며 실제 수익 검증이 아니다. 반복 재생 event_hash 일치 시험 포함. 전체 12절 통과를 선언하지 않으며 ENGINEERING_READY도 아직 부여하지 않는다.

P0에서 기존 원본 증거 파일4개를 실제 읽고 SHA/중복/연속성 계산: 중복제거543개5m, 최장연속284개(23시간40분). 해당 파일들은 추론된1m집계5m이며 원본1m/독립원본5m가 아니고 워밍업150개15m에 필요한450개연속5m 미달. 다른 거래소로 보충하지 않았다. 이4파일 밖의 서버DB 전체 존재 여부를 검사했다고 주장하지 않는다.

공식 문서 `https://www.lbank.com/docs/contract.html`를 읽고 문서에 실제 있는 공개instrument(SwapU)와depth 경로만 요청했다. 실행환경 응답 둘 다 HTTP403. 인증 우회·키·계좌 요청 없음. raw 결과와 시각은 public_probe.json. 문서가 필드를 설명한다는 사실과 실제 값 확인을 구분했다. 현재tick/step/승수/단위/수수료/펀딩일정·이력null. 90일 과거자료 확보 안됨.

## P2 비교 보고

좋아진 기능: 예전 전략과 분리된 A/B/C 고정 규칙·비용미확인차단·120초만료·상태일관성·영속기록 구현. 나빠진 성능: 아직 비교 불가. 모르는 것: 기존 대비순손익/낙폭/빈도/정확도 및7%일수 전부 미측정. 봉인시험0건, 검증된 종료0건. 이는 시장에서0기회가 발생했다는 뜻이 아니라 적격자료 재생을 아직 수행하지 못했다는 뜻이다.

`comparison_report.json`을 실제 자료부족 결과로 제공한다. 최종 봉인시험/전체비용·지연 스트레스/기존전략 동일위험 비교 미완료이며 HISTORICAL_CANDIDATE 미충족. 초기6설정 예산 중 기본규칙3개만 등록, 변경형0. 성적을 보고 숫자 조정한 실험 없음.

## 남은 구현 순서

1. 공급자DB에서 native1m+독립5m의 기간·완료/도착시각·단위를 조회/안전 추출하는 어댑터 완성. 공식 상품 메타/비용·펀딩 자료 확인. 403 자체를 영구 불가능으로 일반화하지 않는다.
2. 펀딩 정산 이벤트·공급자 누락/coverage 인증을 Engine/Store/replay의 같은 입력 트랜잭션에 연결. 지금은 Position의 산식과 planning helper만 제공; verified_net null 유지.
3. 동일분봉 TP1후새손절의 양쪽 순서 성과범위, 완전한日NAV/일7%분모/최대시가DD/가용성·탈락비트 보고·사전봉인·기존baseline 위험정규화 연결. UTC일경계→KST당일 이벤트 순서 추가 검증.
4. 실제도착 기록을 재생해 후보/전이 일치, 12절 전체표 완료 후만 ENGINEERING_READY. 현재31시험만으로 완결 판정 금지.
5. 자료 확보 범위에서 개발45/검토15/봉인30일 실제 구간을 고정. 세 기본형과 통합장부·기존대조군 같은 자료/비용/지연으로 계산. 부족하면 부족한 범위와 null 공개.
6. P3 발송기는 기본 disabled outbox와 TTL/긴급재시도를 연결하되 운영 전환은 이 요청과 별개. 기존 반복작업/알림 재개 승인으로 해석하지 않는다.

설계서13.2의 “매 작업 묶음이 끝나면 결과와 다음 작업을 정리하고 멈춘다”에 따라 이번 묶음은 P0 자료보고+P1 초안/동작시험+P2 자료부족 보고로 마무리한다. 전체 알람 구현 완료나 수익성 개선 완료가 아니다. 이전 끝없는 외부후보 탐색을 재개하지 않는다.
