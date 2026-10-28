# ZB 완료 진행 인수인계 — 2026-10-28

**P1 연구 구현을 실제 완료하고 [draft PR #27](https://github.com/hekyun-ctrl/upbit-market-analyzer/pull/27)에 저장했다. 전체 기능 완료·수익성 개선·운영 승격은 아니다.** P1만으로는 같은 개발 자료의 최종 알람이 3→0건이므로 알람 효용을 해결하지 못했다. 원자료 확인을 먼저 실제 시도했으며 두 검증 차단은 유지한다.

작업 시작: 2026-10-28 03:18 UTC(분 단위), 종료 체크포인트: 2026-10-28T03:41:47.562197+00:00. 단일 작업자 lease는 `research/zerobase/completion_state.json`에 종료했다. 다음 작업자는 최신 head/상태를 다시 읽고 새 lease를 취득한다. 모든 원격 ref 쓰기는 직전 expected SHA를 지정했다.

## 기준과 보존 범위

시작과 종료에 재확인한 head는 동일하다.

| 기준 | SHA / 상태 |
|---|---|
| 운영 `feature/lbank-futures-monitor` | `b4c7d94efb305c19cbe44f050c806440e19351a0` |
| 연구 `feature/lbank-zerobase-zb1-20261021` | `5ea63fea7fb5a312a99702535c31d4dccd8155fd` |
| PR21 `fix/lbank-entry-location-20261025` | `ce33a017ff6aca0729175a8f88c3ec978f4a84a2`, draft/open/unmerged |
| PR22 `research/trade-evidence-p0-20261025` | `d098f5657d823769f468846df1fac5ec04986d0e`, draft/open/unmerged |

PR22 최신에서 `research/zb-completion-20261028` 생성. P0 설계서, README, summary, manifest를 먼저 읽었다. P0는 장부·재생기만 완료이며 P1~P5나 live ledger 완료로 간주하지 않는다. 변경 증거: lease `f57b995`, 구현·실행 `8d31e21`, CSV 원문 바이트·해시 보존 `8c4aa412`. PR27 base는 PR22 연구 branch다.

배포·운영 병합·주문·메시지 전송 없음. 기존 수집기·위험 알림과 paper/ZB outbound=false, orders=false를 변경하지 않았다. Upbit/CAP/ZIL 수정 없음. 1h/4h는 참고이며 새 진입 차단 없음. 12:14 KST 런타임의 verified_closed=0 / baseline_comparison=null / funding_history_days=0는 상위 인수인계 값으로만 보존했다. 이 작업에서는 Railway를 다시 읽거나 수정하지 않았다.

## 먼저 실행한 실제 원자료 확인

`research/probe_structural_target_sources.py`와 `completion_20261028/source_probe/audit.json`에 정확한 URL·UTC 요청/수신·원문 해시를 저장했다.

- [공식 futures 문서](https://www.lbank.com/docs/contract.html)를 웹에서 재확인했다. 공개 time/instrument/market/depth와 공식 WS 주소는 있으나 futures candle 구독·완료 플래그·volume 단위 스키마를 찾지 못했다. spot `/v2/kline.do`를 futures로 사용하지 않았다.
- 로컬 GET: 위 문서 및 `https://lbkperp.lbank.com/cfd/openApi/v1/pub/` 아래 `instrument?productGroup=SwapU`, `getTime`, `marketData?productGroup=SwapU`는 모두 HTTP403, 원문 `error code: 1010`(17bytes). 각 응답 SHA256은 `2938e9f1284180959e33ab1718d0793a72ff6e4cdb8108c34dcd14e69446de5c`다.
- 공식 `wss://lbkperpws.lbank.com/ws`는 2회 연결 성공했으나 문서상 candle 구독 스키마가 없고 관측 프레임이 없었다. 임의 스키마를 공식으로 간주하지 않았다.
- 기존 수집기의 `wss://uuws.rerrkvifj.com/ws/v3` / `BTCUSDT_5m` 알려진 구독을 유한 실행했다. 03:27:52.560107~03:27:58.243651 UTC에 **raw 1m 4개·raw 5m 2개**를 받아 원문 hex와 해시를 보존했다. 1m 시작 03:27, 5m 시작 03:25이고 모든 update 시각이 명목 마감 이전이다. `j="0"`, `tradeTime=true`를 완료 플래그로 해석하지 않았다.
- 원문 완전 중복은 0, 같은 bar key의 누적 수정은 4회(고유 key 2개). source update→로컬 수신 차는 약 0.243~0.639초지만 초 단위 원천 시계 오차를 포함하므로 순수 네트워크 지연이 아니다. 2회 재연결에서 같은 형성 중 bar가 재등장했을 뿐 완료 rollover·영속 재시작 복구를 증명하지 못했다. gaps/완료 연속성은 null이다.
- **같은 feed의 raw5m과 로컬 1m→5m 집계를 독립 근거라고 부르지 않는다.** h(volume), i(turnover) 값만으로 contracts/BTC 의미를 확정하지 않았다. `RAW5M_INDEPENDENCE_UNVERIFIED`, `CANDLE_VOLUME_UNIT_UNVERIFIED`, funding history 0일을 유지한다.

실제 데이터 대기는 독립 5m 원천·공식 단위/완료 스키마, 마감·공백·재연결을 포함한 raw 수신 기록, 펀딩 정산·체결/스프레드 근거다. 누락된 과거 수신 시각을 명목 마감으로 대체하지 않는다.

## P1 단독 구현

`lbank_structural_target_plan.py`는 기존 confirmed pivot 탐색을 재사용해 현재까지 알려진 가장 가까운 미돌파 장벽 앞에 기존 1.5bp(최소1tick) 완충 목표를 둔다. 그 목표·기존 구조 손절을 고정해 비용 후 RR 1.5의 허용 진입 경계를 역산한다. 롱은 최대 진입가, 숏은 최소 허용 진입가다. 가까운 장벽 너머로 목표를 만들거나 손절을 구조 안으로 당기지 않는다. 장벽 미확인은 `room_unknown`, 공간 부족은 `room_insufficient`다.

오른쪽 확인 봉의 수신이 늦으면 피벗도 늦게 사용한다. 실제 known_at이 없을 때는 null을 유지하고 offline에서만 명시적인 nominal delay를 허용한다. 미래 수신 bar가 과거 장벽을 무효화하는 누출을 막고, 반복·공백·forming·NaN을 거절한다. 확인 시 원래 S/T를 보존하며 새 가까운 장벽·원문 수정·기존 장벽 무효화를 거절한다.

공통 `calculate_plan`에 명시적 `target_policy` 인자만 추가했다. 운영 호출자는 이를 전달하지 않으며 활성화 env flag도 없다. 기존 탐지·EMA/RSI/구조/위험 조건·30초 확인·정책별 쿨다운은 그대로다. target distance와 detection/entry delay를 같이 바꾸지 않았다. P1 수량은 과거 공개 instrument의 0.1 USDT tick / 0.0001 BTC step·최소량 / 1USDT 최소 notional을 **현재 미재검증 가정**으로 적용하고 기존 위험·증거금 상한 아래로 내림한다.

독립된 다음 장벽 근거가 없으므로 T2를 RR 필요 가격으로 만들지 않고 T1과 같게 둔다. 동일 half/half benchmark에서 두 절반이 T1에서 끝나는 가정이며 실제 전량 체결이 아니다. 이 청산 가정은 출력에 명시했다.

## 같은 개발 자료의 실제 비교

입력은 보존된 신호 주변 관측 1,068개이며 미사용 연속 검증 구간이 아니다. 기본 0/15초 capture의 **모든 창별 관측·후보·거래 출력**이 저장된 P0/PR21 결과와 정확히 같음을 검사했다. 각 정책의 cooldown과 상태는 독립이다.

| 시나리오 | capture / P1 거래 | 제외 / 추가 / 공통 최종 | capture / P1 모형 순손익 USDT | capture / P1 종료거래 낙폭 |
|---|---:|---:|---:|---:|
| 기존 비용, 명목0초 | 3 / 0 | 3 / 0 / 0 | −28.0779 / 0 | 28.0779 / 0 |
| 기존 비용, 명목15초 | 2 / 0 | 2 / 0 / 0 | −18.7186 / 0 | 18.7186 / 0 |
| 불리한 비용, 명목0초 | 1 / 0 | 1 / 0 / 0 | −9.3586 / 0 | 9.3586 / 0 |

기본0초 capture 3건은 SL 선행, P1은 진입0건이다. 기본15초는 2건 SL, 불리한 비용은 1건 SL이다. P1 순손익0/종료거래 낙폭0은 **미진입** 결과다. 진입 후 미확정은 null이며, P1 미확정 비율은 분모0으로 null이다. emergency 청산 수·완전 평가손익 최대낙폭은 미모형/미확인으로 null이다. 알람을 모두 없앤 결과를 손실 회피 또는 정확도 개선으로 승격하지 않는다.

기본0초의 공통 후보 사건52개, 15초40개는 모두 후보 시각 차0초다. 공통 최종 알람은0이어서 확인 지연 개선을 계산할 수 없다. 기본0초 P1 후보 차단은 `room_insufficient`33회·`room_unknown`7회이며, 나머지 context7/RSI12/EMA위치5/재시험모멘텀4회는 기존 검사다. 15초 공간 부족28/미확인4회다. 이는 평가 횟수이며 고유 거래 수와 다르다. 전체 단계·원인·고유 사건은 압축 decision CSV와 opportunity CSV에 있다.

기존 비용은 taker 각6bp, stop slip2bp, entry당 funding reserve1bp, leverage30, 기준잔고1871.9017·risk0.5%·margin15%의 보존 모형이다. 실제 개인 수수료·보유시간별 펀딩이 아니다. 불리한 비용은 각8bp/stop slip5bp/funding reserve3bp의 가정이다. entry·TP 체결 슬리피지는 여전히 미검증이며 이를 실제 순익으로 보고하지 않는다. 비용 변화에 따른 detector의 기존 range geometry 효과는 각 비용 시나리오 안에서 동일하게 적용했고 P1 효과와 섞어 해석하지 않는다.

수동30/60초 가정은 **관측된 다음 quote**를 사용하고 미관측 tick을 보간하지 않았다. 같은 quote를 사용하는 경우가 있어 독립 거래로 합산하지 않는다. 기본0초 기존3건 중2건은 고정 계획의 허용 RR을 잃어 `no_entry`/0, 나머지1건은 약63.81초 뒤 quote에서 모형 −8.2404USDT다. 실제 수동 체결 개선 주장이 아니다. 관측 누락·후속 불명은 null로 남긴다. 모든 카드·horizon·가정은 `trade_cards.json`에 있다.

## 검증과 재현

새 P1 합성 경계/통합 시험17개를 포함한 관련121개 통과. 합성 가격은 성과 표본이 아니다. 실제 변경한 계획 경로 때문에 비용·구조·geometry·refinement·location·중지 paper invariant를 검사했다. P0 기존15개와 전체467개를 반복하지 않았다. 순수 기본 호출의 보존 출력 일치와 live target_policy 호출0을 별도로 검사했다.

```bash
python -m unittest test_lbank_structural_target_plan test_lbank_fee_risk test_lbank_native_target_room test_lbank_entry_geometry test_lbank_entry_refinement test_lbank_entry_location test_lbank_paper_notifications -q
python research/compare_structural_targets.py
python research/probe_structural_target_sources.py
python research/probe_structural_target_sources.py --analyze-existing
git diff --check
```

`completion_20261028/p1/manifest.json`: 코드·입력·출력 SHA256. `validation.json`: 실행 명령·결과·의존성 버전·소스/산출물 해시. `targeted_tests.txt`:121개 통과 원문. 전체 `gate_decisions.csv.gz`는 mtime=0으로 저장했고 해제 SHA256도 manifest에 있다. CSV 원문은 재생 명령으로 복원하는 중간 파일이다. 원격 `8c4aa412`를 다시 fetch한 뒤 코드·입력·모든 출력·압축 해제 CSV 해시를 재확인했다. 임시 SQLite는 P0 장부 어댑터로 생성하며 전송 의도0을 확인했다. 실제 장부 운영 연결은 아직 아니다.

## 다음 작업: 기술과 실제 데이터 대기

**지금 코드로 진행 가능:** P2에서 E1 포착/E3 확인/E5 초기반전의 독립 실험 manifest를 만들고 P1 목표·비용과 분리한다. 같은 개발 결과를 보며 RR/목표 문턱을 낮추지 않는다. live EvidenceLedger의 실패 격리 어댑터·마이그레이션·재시작·용량/보존 시험도 구현이 남았다.

**실제 자료가 있어야 끝낼 수 있음:** 공식 candle 완료/volume 스키마와 독립5m 근거를 확보하고 raw1m/5m 수신·마감·재연결 연속성을 교차 확인한다. 미사용 연속 자료와 실제 funding/체결 비용이 확보되면 고정된 정책을 동일 사건·동일 위험으로 비교한다. 새 수익성 문턱이나 과거100paper/30일 선행 조건을 추가하지 않는다.

P2~P5, 정식 승격, live ledger 연결·장애 격리는 미완료다. 이번 체크포인트가 끝난 것과 사용자가 원하는 알람·진입 개선이 완료된 것을 구분한다.
