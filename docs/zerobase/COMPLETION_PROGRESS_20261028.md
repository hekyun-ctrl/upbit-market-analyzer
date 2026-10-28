# ZB 완료 진행 인수인계 — 2026-10-28

**P1과 P2의 E3 단독 연구 구현·동일 자료 비교를 [draft PR #27](https://github.com/hekyun-ctrl/upbit-market-analyzer/pull/27)에 저장했다. 전체 기능 완료·수익성 개선·운영 승격은 아니다.** E3의 기존 목표 패널은 기본0초 알람3→5이나 미확정2건으로 전체 순손익·낙폭은 null이다. P1 및 P1+E3는0건이어서 확인 효과를 식별할 수 없다. P2 나머지 실험과 live ledger/P3~P5는 남아 있다.

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

P2~P5, 정식 승격, live ledger 연결·장애 격리는 미완료다. 이번 체크포인트가 끝난 것과 우주심께서 원하시는 알람·진입 개선이 완료된 것을 구분한다.

## 완료 실행 확인 및 거래별 근거 보완

예약 실행 `dc43a56c-99e8-8328-8374-a7585bd7a8df`는 2026-10-28 03:42:43 UTC에 completed로 종료됐다. 종료 결과에는 GPT-6.1 Sol 작업자로 기록돼 있으나 별도의 실행 모델 메타데이터는 도구에서 제공하지 않아 독립 확인한 것으로 보고하지 않는다. 기존 실행과 lease가 살아 있는 동안 구현 작업을 중복 시작하지 않았고, 종료·lease 해제를 확인한 뒤 검토 보완만 단독 수행했다.

P1 재생기가 전체 decision 표는 보존했지만 거래별 `trades.csv`의 `gate_reasons`를 연결하지 않아 빈 배열로 출력한 결함을 수정했다. 같은 event의 실제 거절 기록만 policy·단계·평가 시각·입력 해시·구조 목표 근거와 함께 연결한다. 반복 평가는 하나의 거래에 묶고 관측되지 않은 원인은 만들지 않는다. 신규 회귀 시험 3개 통과. 관련121개 기존 성공 결과는 보존했으며 이 출력 보완 때문에 전체 시험을 반복하지 않았다.

동일 자료를 다시 재생해 모든 기본 창의 capture 출력을 재검증했고 3개 시나리오의 정책별 집계·거래 짝·후보 시각이 보완 전과 동일함을 확인했다. 시나리오 행6개 모두 P1 제외 사유가 채워졌으며, 이는 서로 독립인 거래6건이 아니다. 기본0초의 기존3건은 구조 장벽 미확인1건, 공간 부족2건(그중1건은 다른 평가 시점의 RSI 거절도 존재)이다. 15초·불리 비용의 재사용 사건에도 같은 근거가 연결된다. 전체 decision 표와 입력 해시는 그대로이며 새 코드·카드/표·manifest·validation 해시를 함께 갱신했다. 알람3→0 / 2→0, 조기화0초 및 미진입 결과라는 한계는 바뀌지 않았다.

운영·연구·PR22 head는 시작 값 그대로 재확인했다. 다음 작업은 P1 목표/비용을 고정한 P2 포착·확인 독립 비교와 실패 격리 장부 연결이다. 원본5m 독립성·거래량 단위 차단을 유지하고 운영 변경·발송·주문은 하지 않았다. 검토 lease도 종료했으며 기존 예약 작업은 활성 상태를 유지한다. 보완 완료 시각: 2026-10-28T03:49:17.083Z.


## P2 E3 단독 구현·비교 체크포인트

시작04:20:48 UTC, 사용량 제한 중단 후 같은 단일 작업자로07:15 UTC 재개, 종료 2026-10-28T07:31:26.663502+00:00. 재개 시 운영/연구/PR21/22 및 PR27 최신 head와 lease를 다시 확인했다. 다른 작업자는 없었고 만료한 자기 lease를 expected-head로 갱신했다. 코드·92개 관련 시험(신규19개)·전체 재생 산출물은 보존됐으며, 마지막 검토에서 같은 stop 무효화 사건의 재무장 가능성을 수정하고 해당 경로만 재검증했다. 임의 다른 모델·에이전트·자동화는 만들지 않았다.

선언 commit `d2859fa75c8f7c72c828b7c4250f5d5feb2e49de`를 실행 전에 저장했다. E3 구현·모든 비교/해시 증거 commit은 `a9f4eddbe6be77070da701ed1c7b3c0afeb2ebd6`다. [P2 전체 보고서](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/a9f4eddbe6be77070da701ed1c7b3c0afeb2ebd6/research/zerobase/completion_20261028/p2/README.md), `p2/{experiment_manifest,manifest,report_manifest,validation}.json`에 고정 정의·명령·입출력 해시가 있다. Git HTTPS push는 재개 환경의 자격증명 부재로 실패해 GitHub connector의 blob/tree/commit 및 expected-head ref 갱신으로 저장했다. 업로드한 모든 blob SHA와 전체 tree를 로컬 구현과 비교했다.

E3만 실제 age를 유지하며 완료 구조의 고정 trigger·관측 quote·기존 위험/비용/RSI/맥락/추격/TTL 검사에서 준비된 첫 평가에 확인한다. 발견 시 이미 충족되면 같은 관측에서 확인하지만 독립 시장 근거 두 개라고 부르지 않는다. 고정30초를 제거하는 명시적 연구 인자만 추가했고 운영 호출은 기본30초다. 첫 사후 완료봉부터 원래 stop을 확인하며 stop 침범 사건은 개발 창 내에 기억해 이미 계산된 proposal·이후 회복으로 같은 사건을 재무장하지 못한다. 새 구조 사건만 허용한다. P1 목표·RR1.5·비용·1h/4h 참고 원칙은 고정했다.

| 시나리오 | capture→E3 알람 | 공통/제외/추가 | E3 SL/미확정 | E3 전체 모형 순손익·종료낙폭 |
|---|---:|---:|---:|---|
|기존 비용·명목0초|3→5|2/1/3|3/2|null/null|
|기존 비용·명목15초|2→4|1/1/3|2/2|null/null|
|불리 비용·명목0초|1→0|0/1/0|0/0|0/0 미진입|

기본0초 공통 확인은0초·62.180124초 조기화,15초는 같은 숏의 재사용이다. 후보 시각 차이는0이다. 공통 숏도SL이며, 더 일찍 관측된 계획의 S/T를 양쪽 quote에 고정한 별도 귀속 모형은 E3−9.359357USDT/늦은 quote는RR 상실로 미진입0이다. 실제 정책의 재계산 목표 변화와 시간 효과를 분리했다. 추가3건은SL1/미확정2다. 확인 평가가 보존 자료에 없는 비교군은 그 사실을 명시하고 실패 이유를 만들지 않는다. 모든 거절·단계·고유 사건·후속 결과·수동30/60초는 카드/CSV/`event_accounting.json`/`fixed_plan_attribution.json`에 보존했다. 전체 baseline6개 arm×시나리오의 창별 경제 출력은 이전 고정값과 정확히 같았다.

P1/P1+E3는 세 비용·지연 시나리오 모두0건/확인 평가0회다. 미진입0은 개선이 아니고 분모0 미확정 비율은 null이다. E3 전체 순손익/낙폭은 미확정, emergency/전체MTM 낙폭도 null이다. 수신·Telegram·체결 지연 개선을 입증한 것이 아니다. 재사용 개발 자료이고 수수료6/8bp·stop slip2/5bp·가정 funding1/3bp, 실제 펀딩0일과 체결/스프레드 미검증을 유지한다. 독립5m·volume 단위 차단과 연속성/미사용 평가 부재도 그대로다. 기존 probe·P0 15개·legacy467개는 반복하지 않았다.

다음은 E1 첫 재시험 포착 단독 arm과 E3 조합의 분리 비교, 별도의 실패 격리 live ledger 어댑터다. E4/E5·장부 migration/restart/retention·P3 연속 실수신 비교·P4/P5는 미완료다. 수익 문턱을 낮추거나 새100paper/30일 선행 조건을 추가하지 않았다. 운영·연구·PR21/22 head는 시작 값 그대로이며 무발송·무주문·배포/운영 병합/Upbit 수정 없음. 완료 state의 단일 lease를 해제했다.
