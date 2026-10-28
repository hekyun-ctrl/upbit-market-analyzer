# ZB 완료 진행 인수인계 — 2026-10-28

최신 체크포인트: E1 첫 재시험 포착 단독과 E1/E3 조합을8개 arm으로 구현·비교하고 원격 검증했다. 후보는 늘었지만 최종 알람과 경제 출력은 해당 대조군과 같고, 일부 E3 결과는 미확정이다. 전체 기능·수익성·운영 승격 완료가 아니다.

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

## P2 E1 탐지 단독·E1/E3 조합 체크포인트

시작 2026-10-28T08:08:26.034318+00:00, 종료 2026-10-28T08:25:19.509976+00:00. 최신 운영/연구/PR21/22/27과 lease를 먼저 다시 읽었고, 이전 lease가 비활성·head불변임을 확인했다. 단일 lease와 실행 전 선언은 `be8191e9f9280acd207ce9c95ae966c754b6b2d8`, 코드·시험 중간 저장은 `546bd05a573761fd9adedd76f2bd45da81370663`, 전체 비교 증거는 `097a07193e9e75b792300c9658bb547adecc2132`에 저장했다. 같은 모델의 단일 작업자로 진행했으며 추가 작업자·모델 대체·자동화는 만들지 않았다.

[전체 E1 보고서](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/097a07193e9e75b792300c9658bb547adecc2132/research/zerobase/completion_20261028/p2_e1/README.md). `p2_e1/{experiment_manifest,manifest,report_manifest,validation}.json`에 선언·명령·UTC·입출력 해시가 있다.8개 arm은 capture/P1 목표×기존/E1 탐지×기존/E3 확인이며6개 비교로 요소를 분리했다. 기존4개 arm×3개 시나리오의 전체 창별 출력12개는 이전 E3와 정확히 같고, E1 쪽 최종 알람 계획·시각·가격·후속 결과12개도 해당 탐지 대조군과 같다. 후보·거절 출력은 실제로 달라졌으며 같은 것으로 표현하지 않는다.

PR21 `first_retests` 원문을 그대로 연구 어댑터로 재사용했다. 첫 재시험 proposal의 기존 strategy/direction 우선순위만 주입하고, 운영 capture 모드·기존 재시험 momentum(기존 우선순위 예외 포함)·맥락·RSI·목표·비용·RR1.5·추격·30초 확인은 유지한다. location 묶음 모드의 momentum 우회·위치/장벽 검사·확인 진전은 섞지 않았다. E3 조합만 기존 근거 확인/실제age/고정trigger/stop 무효화 유지 구현을 사용한다. E1 탐지 단독이며 PR21 전체 또는 초기반전 경로 완성이 아니다.

| 시나리오 | capture→E1 raw후보 봉계열 | capture/E1 알람 | E3/E1+E3 알람 | E1+E3 SL/미확정 | P1 모든 조합 |
|---|---:|---:|---:|---:|---:|
|기존비용·명목0초|67→77|3/3|5/5|3/2|진입0|
|기존비용·명목15초|53→59|2/2|4/4|2/2|진입0|
|불리비용·명목0초|65→75|1/1|0/0|0/0|진입0|

기본0초 첫 재시험14회/고유앵커12개는 모두 초기 심사에서 탈락했다(위험/목표10·기존momentum3·RSI1). 위험/목표 세부는 기존 목표 패널에서 장벽5·EMA추격2·관측범위밖목표3, P1은 공간부족8·EMA추격2다. 기본15초는10회/8개로 위험/목표9·RSI1이다. 실제 quote 포착2회와 완료봉포착12회가 기본0초14회를 구성하며, 반복 평가·비용/지연 변형을 독립 표본으로 합산하지 않는다.

고유 raw사건은 기본0초52→61(공통52/추가9),15초40→46(공통40/추가6)이다. 공통 후보의 signed old-minus-new 시차는0 또는−130.565779초다. 음수인 한 정확앵커는 E1 평가가 더 늦었고, 같은 사건이 시나리오에 재사용됐다. 새 앵커에 가짜 대조시각을 만들지 않는다. E1 최종 알람은 모두 공통·확인시차0/추가제외0이다. E1 내 E3 효과는 이전과 동일한 기본0초 공통2/추가3/제외1,15초1/3/1이며, 기존 E3의0/62.180124초 및SL/미확정 결과가 재사용된 것이지 새 성과 표본이 아니다.

E1 기본0초 모형 순손익/종료거래낙폭−28.0779196/28.0779196USDT,15초−18.7186398/18.7186398,불리비용−9.3586427/9.3586427로 해당 기존 탐지와 같다. E1+E3 기본0/15초 전체 손익·낙폭은 미확정2건 때문에null이다. 불리 E1+E3/P1조합의0은 미진입이고 분모0 미확정비율은null, emergency·전체MTM낙폭도null이다. 수익·알람효용 개선을 입증하지 못했다.

44개 관련 시험(신규E1 합성10개) 통과. 코드·입력·산출물·압축해제CSV 해시와 미진입0/미확정null·사건별 관측거절 또는 비교확인 부재를 검사했다. 실제 실패가 없던 비교군에 이유를 만들지 않았다. 후보/거절/공통·제외·추가/후속결과/고정최초S/T시간귀속/수동30·60초 가정은 전부 보존했다. P0 15개·legacy467개·기존 HTTP/WS probe·이번 재생을 반복하지 않았다. 업로드16개 blob SHA를 로컬과 비교하고 commit을 다시 fetch하여 전체 tree `041afbbee29dcb61882377c619ae6a764028cd7a`가 로컬과 같음을 확인했다.

운영/연구/PR21/22 head는 시작과 같고 PR21/22/27은 open/draft/unmerged다. 생산 모니터·PR21 탐지·P1·E3 원문은 변경하지 않았다. 발송/주문/배포/운영병합/Railway/Upbit·CAP·ZIL 변경은 없다. 독립5m·volume단위·최종봉/수신연속성·펀딩0일·실체결/미사용 연속 자료 부족은 그대로 유지한다. 후보증가를 위해 문턱을 낮추거나 목표를 연장하지 않았고100paper/30일 선행조건도 추가하지 않았다.

다음 기술 작업은 E4 장벽예외·추격과 E5 초기반전의 독립 선언/구현/필요조합, 별도 실패 격리 live EvidenceLedger migration/restart/outage/retention이다. P3~P5는 미완료다. 실제 자료대기는 기존 두 source차단과 funding/receipt/continuity 근거다. 이번 단일 lease를 해제했으며 다음 작업자는 최신head와 새lease부터 시작한다.

## P2 E4 장벽 예외·추격 단독/조합 체크포인트

시작 2026-10-28T09:30:48.819727+00:00, 종료 2026-10-28T09:49:30.321965+00:00. 최신 운영/연구 feature·PR21/22/27과 비활성 lease를 먼저 확인했다. 새 단일 lease·실행 전16-arm 선언은 `116f43b7a89a5c6a6080b23062f703a3f656010e`, 초기 코드·시험 저장은 `9b7aa8a441455d07cf60c3477dc6ed223745e706`, 전체 비교 근거는 `b97a75c97d154b59561f396750c42c7da15acb0c`다. [전체 E4 보고서](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/b97a75c97d154b59561f396750c42c7da15acb0c/research/zerobase/completion_20261028/p2_e4/README.md). 별도 작업자·모델 대체·자동화를 만들지 않았다.

E4는 PR21 `location_check`를 그대로 계획 계산의 후보/확인 두 단계에서 사용하며, 그 계산 안에서만 `may_override_single_pivots=False`로 둔다. location 묶음 모드·탐지·momentum 우회·추가 확인 진전은 켜지 않았다. capture RSI/맥락/30초·TTL·위험·비용·RR1.5·목표 알고리즘과 P1/E1/E3 원문을 고정했고 1h/4h는 참고뿐이다. 기존 현재 ATR 위치 문턱과 별도로 공통 진입가격 비교는 동일 발견 ATR을 사용한다. 장벽 미확인은 기존 목표 패널을 유지하며 P1 `room_unknown`을 완화하지 않았다.

|시나리오|capture→E4|E3→E3+E4|E3+E4 SL/미확정|모든 P1 조합|
|---|---:|---:|---:|---|
|기존 비용·명목0초|3→1(미확정)|5→2|1/1|진입0|
|기존 비용·명목15초|2→1(미확정)|4→2|1/1|진입0|
|불리 비용·명목0초|1→0|0→0|0/0|진입0|

E1을 더한 최종 결과는 위 대응 arm과 같다. 기본0초 capture의 기존3건은 위치/추격에서 모두 제외되고 미확정1건이 추가됐다. E3 비교는3SL+1미확정 제외,1SL 추가,1미확정 공통이다. 추가 SL은 기존에도 같은 시각 심사 통과한 breakout이지만 앞선 동방향 trend-retest가 선택됐던 사건이다. E4가 앞선 후보를 막으면서 기존 우선순위 경로에서 breakout이 선택된다. E4 단독의 추가 미확정은 첫 위치 실패로 기존 watch가 유지되고, 대조군은 이후 breakout freshness에서 취소됐던 상태/검사 순서 상호작용이다. 관측되지 않은 확인 실패를 만들지 않고 peer 후보/선택 사실을 보존했다.

기본15초 E3+E4는 SL1+미확정1 공통, SL1+미확정1 제외다. 공통 E4 확인 시차·가격 차는0이며 조기화 개선이 없다. E4 위치 검사를 통과한 뒤 원래 true였던 pivot 예외는0회라 이번 실제 자료에서 장벽 예외 제거 자체의 추가 경제 효과는 분리 입증할 수 없다. native 장벽 거절 횟수를 모두 신규 E4 제외라고 부르지 않는다. 도달 가능한 단일 장벽 예외 차단은 합성 시험으로 검증했다.

E4 기본0/15초 전체 모형 순손익·종료거래낙폭은 미확정으로 null이다. E3+E4도 각1건 미확정이라 null이며, 추가 SL 모형은 −9.3594223USDT다(기본0초, 기존 비용). 불리 E4/P1의0은 미진입이며 미확정 비율 분모0은 null, emergency·전체MTM낙폭은 null이다. 손실 사례 제외·알람 감소를 수익·효용 개선으로 승격하지 않았다. 수수료6/8bp·stop slip2/5bp·가정 funding1/3bp, 실제 펀딩0일·수신/체결/스프레드 미검증·재사용 개발 구간 한계는 그대로다.

16개 arm×3개 시나리오에서 기존8개 arm의 전체 창별 경제 출력24개가 정확히 재현됐다. 전체36개 거래 비교 행(독립 거래36건 아님),1,094 E4 평가, 실행되지 않은 검사 null, 단계별 고유사건 분모·모든 거절·추가/제외 후속 결과·최초 고정 S/T 시각귀속·공통ATR·수동30/60초 시나리오를 보존했다. 58개 관련 시험(신규14개)과 추가 정확 경계1개를 각각 통과해 고유59개/신규15개다. P0 15개·legacy467·HTTP/WS probe·전체 replay를 반복하지 않았다.

`p2_e4/{experiment_manifest,manifest,report_manifest,validation}.json`에 UTC·명령·코드/입출력/압축해제 SHA256을 보존했다. 표준 CSV CRLF를 기본 staged whitespace가 경고(exit2)하여 CRLF를 허용한 동일 staged 검사는 통과(exit0)했고 원문 byte는 유지했다. 일시 exec 연결 끊김은 즉시 복구됐고 자료 손실이 없었다. 업로드19개 blob SHA와 다시 fetch한 비절단 전체 tree `4843c3f5153b9b3dca009fbd56084d5fa0e4b00e`의 로컬 일치를 확인했다.

운영 b4c7d94/연구5ea63fe/PR21 ce33a01/PR22 d098f56은 시작과 같고 PR21/22/27은 open/draft/unmerged다. 발송/주문/배포/운영병합/Railway/Upbit·CAP·ZIL 변경은 없다. 독립5m·volume단위 차단·공식 완료/수신 연속성·funding/실체결·미사용 연속 자료 부족을 유지한다. 단일 lease를 해제했다.

다음은 실제 설계의 E5 초기 반전(관측 이탈/회복→완료1m 회복 구조→작은 반대 고저점 돌파, RSI·5/15분 추세는 설명 변수)의 별도 선언·구현과 필요한 조합이다. 하드 자료·비용·구조 위험·RR/목표를 고정하고 재사용 결과에 맞추지 않는다. 실패 격리 live EvidenceLedger migration/restart/outage/보존, P3~P5도 미완료다. 기술 체크포인트와 전체 완성·수익 입증을 구분한다.


## P2 E5 실행 전 선언·단일 lease

2026-10-28T09:55:58.304189+00:00에 실제 운영/연구/PR21/22/27 head와 비활성 lease를 확인했다. head는 이전 값 그대로다. E5의10개 사전 완료봉 지지·저항→최근3봉 sweep/첫 방향 회복→고정 작은 반대 고저점의 실제 quote 돌파를 실행 전에 선언했다. RSI·5/15분 추세는 E5만 설명 변수로 실행·기록하고 기존 전략은 그대로다.32개 arm(기존16+E5 대응16),3개 비용/지연 시나리오를 고정하며 기존48개 arm×시나리오 전체 창 출력을 재검증한다. 목표·RR1.5·비용·구조위험·하드자료를 고정하고 결과에 맞추지 않는다. 아직 구현/비교 완료가 아니다. 발송·주문·배포 없음.


## P2 E5 초기 반전 구현·32-arm 개발 비교 체크포인트

시작2026-10-28T09:55:58.304189+00:00, 종료2026-10-28T12:41:40.592643+00:00. 최신 운영/연구/PR21/22/27과 이전 비활성 lease를 확인하고 단일 작업자로 진행했다. 최초 선언 `884da546f96ed88ccb813649af8ff24490521d3b`, 최종 전략 코드 `826c930a3c5efa9165dc902427bbabbf9f1fdf69`, 보고서 코드 `e9b0420212aa4edae5f0ded0795eabee94fff5fd`, 전체 비교/보고서 산출물 `7fd30103af06baf2863b74e2e9e058137a2b4b04`다. [전체 E5 보고서](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/research/zb-completion-20261028/research/zerobase/completion_20261028/p2_e5/README.md). 사용량 중단 뒤 같은 작업자가 재개했을 때 수정본 비교와 보고서는 모두 완료돼 있어 반복하지 않았다. 최신 head가 e9b0420 그대로이고 자기 lease만 만료했음을 확인해 expected-head로 갱신했다. 재개 state의 임시 텍스트 전송 잘림은 완전 byte/hash 검증 JSON `ae75e860`으로 즉시 선형 수정했다. 다른 작업자·모델 대체·자동화는 만들지 않았다.

E5는 이미 알려진10개 완료1m의 지지/저항 이탈→최근3봉 중 첫 방향 회복 완료 구조→고정 작은 반대 고저점의 실제 quote 돌파다. level+sweep의 정확한 원문 합성 해시로 사건을 고정하고, 첫 사후봉/quote의 구조·원래 stop 침범과 회복선·유효기한을 검사한다. 초기 비교는 결과를 보기 전에 발견한 breakout 우선순위/서로 다른 sweep ID 문제 때문에 exit130으로 중단했고, 원래 우선순위와 사건 구분·preempted readiness null을 고친 수정본 완전 비교1회만 분석했다. 파라미터/성과 문턱 튜닝이 아니다. attempts.json에 시도를 남겼다.

E5만 RSI/5·15분 역추세를 실행·기록한 설명 변수로 다루고, 자료 누락·평탄 시장·late5m/EMA/가격 추격·구조 위험·비용·RR1.5·목표·수량·쿨다운은 유지했다. stop은 기존 기본5m geometry를 쓰며 줄이지 않았다. E3의 비breakout 마지막 완료봉 trigger, E4 원문 위치 검사와 P1 목표는 조합 안에서도 그대로다. 운영/P1/E1/E3/E4·장부 원문은 변경하지 않았고1h/4h는 참고뿐이다.

32개 arm×3개 시나리오에서 기존16개 arm의48개 전체 창 출력이 정확히 재현됐다. E5를 더한48개 최종 알람 출력도 각 대조군과 정확히 같다. 추가/제외0, 공통 확인 lead/진입가차0이다. 기본0초 capture3→3(3SL),E3 5→5(3SL/2미확정),E4 1→1(미확정),E3+E4 2→2(1SL/1미확정)다.15초는 각각2→2,4→4,1→1,2→2다. 모든 P1 조합 진입0이고 불리 비용도 대응 대조군과 같다. E5 경로 자체 최종 진입0이어서 수익·빈도·효용 개선을 입증하지 못했다.

기본0초 E5 후보96회/고유72사건 중 초기 통과2건은 실제 global_cooldown으로 막혔다.15초는79회/60사건 중2건 통과,1건 cooldown·1건 watch 진입 뒤64.408880초 trigger 미돌파/120.418500초 sweep 만료다. E1/E3 대응 변형은 같은 사건 재사용이다. 기본0초 초기 거절은 기존 목표범위40·평탄시장30·장벽24, P1은 공간부족65·평탄30·미확인1이다. E4는 위치/추격을 추가 차단한다. 관측하지 않은 확인 실패를 만들지 않았다. 원문 candidate/peer/선택/watch 사실을 별도 보존했다.

4336개 E5 후보 평가·4340개 후보/확인 설명 기록은 원래 input_hash와 정확히 같은 상태로 복원했다. 실제 미실행 gate는 not_evaluated로 유지하며 사후 설명 계산을 원래 실행으로 바꾸지 않는다. 구조 scan33552·설명 gate5824·거래 비교42행은 반복 평가/조합/비용·지연 변형이지 독립 표본 수가 아니다. 모든 거래 카드·제외/추가·후속 결과·동일 최초 S/T 시간 귀속·공통 발견 ATR·수동30/60초 가정을 보존했다.

기본 capture+E5 모형 순손익/종료거래낙폭−28.0779196/28.0779196USDT,15초−18.7186398/18.7186398로 기존과 같다. 미확정이 남은 E3/E4 조합 전체 순손익·종료낙폭은 null이며, 미진입0을 개선으로 해석하지 않았다. 분모0 미확정 비율·emergency·전체MTM 낙폭도 null이다. 수수료6/8bp·stop slip2/5bp·가정 funding1/3bp, 실제 funding0일·실체결/스프레드/수신 지연 미검증을 유지한다. received/known_at는 null이고 명목0/15초를 실수신으로 부르지 않는다.

초기 관련60개와 수정 후 최종 E5 22개 통과, 중복 제외66개/신규22개다. 순수 Python 연구 import 캐시는 동일 source/input SQL만 생략하고 receipt revision·모든 decision future 검증을 유지하며 신규2개 무결성 시험으로 확인했다. P0 15개·legacy467·동일 HTTP/WS probe·완료 비교를 반복하지 않았다. manifest/report_manifest/validation에 코드·입력·출력·압축해제 SHA256과 실제 명령/UTC/의존성이 있다.20개 신규 업로드 blob과 다시 fetch한 비절단 전체 tree `435d72d778fcb736616e07ac1e75adc56687ca6e`의411개 blob 모두 로컬 index와 일치했다.

운영b4c7d94/연구5ea63fe/PR21ce33a01/PR22d098f56은 시작과 같고 PR21/22/27은 open/draft/unmerged다. 발송·주문·배포·운영병합·Railway/Upbit·CAP·ZIL 변경 없음. RAW5M_INDEPENDENCE_UNVERIFIED·CANDLE_VOLUME_UNIT_UNVERIFIED, 공식 완료/volume/독립5m/수신 연속성·실제 펀딩/체결·미사용 자료 부족을 유지한다. 6개 같은feed forming frame을 독립자료로 승격하지 않았다. 수익 기준을 낮추거나100paper/30일 선행조건을 추가하지 않았다.

P2는 E1~E5 독립 연구 구현·필요 조합의 개발 비교까지 완료다. 다음 기술 작업은 실패 격리 live EvidenceLedger adapter의 versioned migration/restart/outage/보존 연결이다. P3 실제 연속 수신/미사용 비교·P4 정식 승격 보고·P5 승인된 적용은 미완료다. 전체 기능 완료·수익 입증으로 보고하지 않으며 이번 단일 lease만 해제한다.


## Failure-isolated live ledger implementation lease

Started 2026-10-28T12:52:48.754373+00:00. Actual production/research/PR21/22/27 heads and inactive lease verified; PR27 starts at1a30b248. Sole worker claims the lease with expected-head writes. Next: disabled-by-default bounded asynchronous EvidenceLedger adapter, versioned migration, idempotent restart, outage/gap audit, safe retention with archive hashes, and meaningful failure injection plus retained-real-data integration checks. No strategy/replay retuning, sends, orders, deployment or production merge. P3~P5 and source blockers remain.


## 실패 격리 live EvidenceLedger 구현 체크포인트 — 운영 활성화 전

시작2026-10-28T12:52:48.754373+00:00, 종료2026-10-28T13:12:10.385604+00:00. 실제 운영/연구/PR21/22/27과 비활성 lease를 먼저 확인했다. 시작 PR27은1a30b248, 단일 lease392ec8ed, 초기 코드436656a6, 최종 코드·전체 증거는 `9b0be7aa99b4eefc72a7f2af1626d2c7824ed7d1`이다. [전체 구현/한계 보고서](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/9b0be7aa99b4eefc72a7f2af1626d2c7824ed7d1/research/zerobase/completion_20261028/live_ledger/README.md) · [manifest](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/9b0be7aa99b4eefc72a7f2af1626d2c7824ed7d1/research/zerobase/completion_20261028/live_ledger/manifest.json).

선택적 sidecar를 native 원문 wire/정밀 수신 시각과 기존 monitor의 관측·후보·확인/만료·알람 자격·고정 계획·기존 알림 결과·후속 관측에 연결했다. 기본 false이며 활성화/배포하지 않았다. SQLite는 daemon worker만 열고 producer는 크기 제한 JSON snapshot과64개 nonwaiting queue를 사용한다. core EvidenceLedger transaction을 중첩 가능하게 해 envelope와 파생 source/decision/plan/불활성 intent/미확정 결과를 원자적으로 기록한다. additive SQLite version0/1→2 migration, 미래 버전 사전 거절, 재시작 dedup·unclean session의 lost_records:null, outage/queue 손실 gap을 구현했다.

후속 검토에서 같은 가격의 새 봉으로 anchor를 다시 고를 수 있는 문제를 발견해 후보 record hash를 watch→확인→고정계획에 전달하도록 수정했다. 재시작 뒤에도 원래 후보 anchor를 유지하고 후보 기록 유실은 명시적으로 unmatched다. source 원문 wire byte/hash도 parsed frame과 함께 보존한다. source blocker는 유지한다. monitor snapshot의 실제 봉 수신/known_at는 null이며 연구 전체 gate trace 연결을 주장하지 않는다. 후보·미진입과 계획 이후 unresolved/null, 관측 exit와 실제 체결, boolean transport 성공과 provider ACK를 구분한다. 기존 transport는 ID가 없어 true/모호 timeout도 send_ack_unknown이다. 새 sender/주문/재전송은 없다.

기본 main DB64MiB·envelope100,000개 상한과 WAL checkpoint/포화 차단을 구현했다.64MiB는 전체 물리 파일 합계가 아니며 main+WAL은 한 transaction 동안 약2배+SQLite 부가량까지 고려한다. cap에 닿으면 분석 기록을 중단하고 미완료 비교·미확정 계획을 지우지 않는다. 새 경로로 integrity/foreign-key 검증 backup과 SHA256/byte/건수/comparison-reference manifest를 만드는 archive API가 있다. 실제 archive 운영·자동 prune/rotation/deletion은 수행하지 않았다. 완료 비교/보존 정책 검토 없이 공간을 만들려고 자료를 지우지 않는다.

관련137개 통과 뒤 anchor11개(신규2)·원문 wire12개(신규1)를 통과해 합집합140개/신규25개다. 마지막140개 단일 suite 실행을 뜻하지 않는다. core transaction 변경 때문에 P0 15개와 관련 회귀를 다시 확인했으며 전체 P0/P1/E1~E5 비교/HTTP·WS probe는 반복하지 않았다. 저장 worker가 실제로 막히고 queue가 가득한 동안 실제 NativeCandleStore.ingest와 SignalRiskTracker.check가 약0.0101초에 진행했다. 합성 fixture의 위험 event1개/native row1개를 보존했다. 실제 개발 관측12개(기존 qualified 후보6개)의 입력과 정책 출력 hash는 정상/강제 SQLite abort 뒤 같았다. 기존 raw6개는 원문 byte/hash/실제 수신 시각 그대로고4개1m/2개5m forming이며 새 요청·독립5m·검증 완료봉0이다. 새 독립 거래/성과/미사용 자료가 아니다.

artifact 저장용 test-report가 d의 list/dict 형태를 하나로 가정해1개 오류를 냈다. 실패 로그를 보존하고 reporting 추출을 고친 뒤 그 test만 통과시켰다. runtime 실패·전략 튜닝이 아니다. 이후 wire 변경의 관련 실행도 통과했다. code/input/output23개 SHA256을 검증했고17개 업로드 blob과 비절단 전체 remote tree `7dd683a5c6afe28a2821f4aa5e32f6c8096f6384`의425개 blob이 local index와 같다.

운영b4c7d94/연구5ea63fe/PR21ce33a01/PR22d098f56은 시작과 같고 PR21/22/27은 open/draft/unmerged다. P1/E1/E3/E4/E5 원문·기존 E5의32×3/48개 대조·48개 최종출력 비교 증거와 별도 ZB EventStore/engine도 unchanged blob을 확인했다. RAW5M_INDEPENDENCE_UNVERIFIED·CANDLE_VOLUME_UNIT_UNVERIFIED, 공식 final/volume/독립5m/실수신 연속성·funding/체결 근거 부족은 그대로다. 수수료6/8bp·stop slip2/5bp·가정 funding1/3bp와 실제 funding0일, 수동/명목 지연 변형의 비독립성을 유지한다.1h/4h 참고뿐이며 수익 문턱 완화·100paper/30일 선행조건 추가가 없다. 발송·주문·배포·운영병합·Railway/Upbit·CAP·ZIL 변경·추가 작업자/모델 대체/자동화 생성 없음.

다음 기술 작업은 sealed received-stream importer와 정확한 raw occurrence/revision→완료봉→snapshot의 causal binding 및 재시작/gap provenance다. P3 전체 실제 gate trace·독립 연속 상태/미사용 비교, 검토된 archive/retention 운영, P4 qualified review와 P5 승인된 적용은 남아 있다. 이번은 비활성 live 연결 코드의 로컬 구현/검증 완료이며 실제 운영 연결이 동작한다고 보고하지 않는다. 전체 기능·수익성 완료가 아니고 단일 lease만 해제했다.


## Sealed received-stream importer lease

Started 2026-10-28T14:38:39.904347+00:00. Actual production/research/PR21/22/27 heads and inactive lease verified. Sole worker, expected-head writes; baseline PR27 8e2f3009. Implement exact occurrence/revision/final-bar/snapshot causal binding with sealed bounded input, restart/gap provenance and fail-closed schema/final/receipt validation. Retained six forming frames remain unqualified. No policy changes, live activation, sends, orders, deployment or repeated completed comparisons. P3-P5 and source blockers remain.


## Sealed received-stream importer checkpoint — actual feed remains unqualified

Started 2026-10-28T14:38:39.904347+00:00; ended 2026-10-28T14:52:59.313403+00:00. Actual production b4c7d94/research5ea63fe/PR21ce33a01/PR22d098f56 and PR27 8e2f3009 were checked first with inactive lease. All four required PR22 design/P0 files match their actual immutable blobs. Sole lease56393786, importer code2603092a, full code/artifacts **ebea0f0af9c8d6e762e95b5aeafc0a8598ffd698**. [Report](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/ebea0f0af9c8d6e762e95b5aeafc0a8598ffd698/research/zerobase/completion_20261028/received_stream/README.md), [manifest](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/ebea0f0af9c8d6e762e95b5aeafc0a8598ffd698/research/zerobase/completion_20261028/received_stream/manifest.json), [validation](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/ebea0f0af9c8d6e762e95b5aeafc0a8598ffd698/research/zerobase/completion_20261028/received_stream/validation.json).

Implemented bounded, inert, atomic sealed segment import with exact wire bytes/hash/original UTC receipt text, distinct occurrences, content revisions, chain/sequence checks, explicit session restart/gap provenance and fail-closed unknown schema/final/receipt validation. Native snapshot references the exact latest final occurrence/bar index and never derives independent5m. Optional live envelope binding preserves the original candidate occurrence/known-at through plan/restart; legacy unavailable receipts remain null, quote/all-input availability and full actual gate vectors remain unverified. Archives inventory sealed hashes/snapshot references without deletion. The built-in chart decoder has final:null, no official final adapter; test-only synthetic final decoders do not qualify real data. Seals establish integrity, not authenticity or receipt continuity.

76 focused tests pass including26 new; related retained-policy input/output regression remains unchanged. The first default-PYTHONPATH run had two ccxt import errors, preserved in environment_resolution.json; unchanged previously installed dependencies resolved them, with no stubs/install. Six retained actual forming frames preserve all original bytes/receipt strings and two finite connection attempts as10 records (6 wires,2 restarts,2 end gaps/loss:null). Reimport identical. All6 explicit current offline final-binding attempts reject FINAL_RECEIPT_UNVERIFIED and roll back; verified finals0/snapshots0/new independent samples0.15 artifact/code/input hashes and SQLite integrity/foreign keys validated. Nontruncated remote tree db9ad1bff84d1e3ba97fc33c17723de2c7a86e75 has439 blobs exactly matching local index.

Production/research/PR21/22 heads remain unchanged and PR21/22/27 stay open/draft/unmerged. Prior frozen P1/E1/E3/E4/E5 and separate ZB code/artifacts unchanged; no repeated full replay/probe, no thresholds relaxed or100paper/30day prerequisite,1h/4h reference only. E5 prior48 contrasts/final outputs remain equal with0 E5 entries; prior E3/E4 unresolved null outcomes and P1 no-entry0 unchanged. Existing fees/slip/funding/manual-delay assumptions remain development assumptions, not actual receipts/fills. Source blockers RAW5M_INDEPENDENCE_UNVERIFIED/CANDLE_VOLUME_UNIT_UNVERIFIED remain. No sends/orders/deployment/production merge, Railway/Upbit/CAP/ZIL edits, other workers/models or new automations.

Original-design acceptance is assessed explicitly in the report. This closes the importer mechanics unit, **not whole feature/P3/P4/P5 or profitability**. Remaining technical work: full actual live gate traces and exact quote/all-input provenance; independent continuous policy-state orchestration and unseen comparison with no-signal intervals, all exclusions/additions, timing and frozen outcomes/costs; reviewed retention operation/runtime enablement. Actual data specifically waits on official final/volume definition, independent native5m alongside1m and continuous rollover/reconnect receipts plus funding/fill/spread evidence. P4 qualified promotion decision and P5 authorized application remain unqualified/unperformed. Next worker starts by checking current heads/inactive sole lease, then exact live gate tracing and independent comparison-state orchestration, preserving frozen strategies. This sole lease is released.
