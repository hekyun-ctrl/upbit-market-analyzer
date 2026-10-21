# LBank 대체 알람 연속 연구

사용자 요청: 일회성 검색으로 끝내지 않고 검증된 대체 알람 확보와 배포 확인까지 검색·검증을 이어간다.

연속성은 research/continuation/state.json을 기준으로 유지한다. 매 실행에서 원전 조사, 데이터 감사, 고정 규칙 재현, 독립 평가, 전향 모의 관찰, 통합 검증, 배포 확인 중 실제 다음 단계 하나를 진전시키고 코드·근거·결과·다음 작업을 연구 브랜치에 저장한다. 실패 후보는 탈락 이유를 기록하고 후속 후보로 이동한다. 원문 확인 없이 수익성을 주장하지 않는다. 작업이 도중에 끝나도 다른 실행이 이어받을 수 있는 체크포인트를 남긴다.

완료 판정은 문서 docs/lbank_strategy_rebuild_2026-10-20.md의 사전 승격 기준과 증거 충족, 실제 운영 통합, Railway SUCCESS, 배포된 소스와 런타임 적용 확인이다. 테스트 통과나 일부 과거 양수만으로 완료하지 않는다. 최소 전향 30일과 종료 100건 등 기준은 보장 일정이 아니다. 결과를 보고 기준을 낮추지 않는다.

수동 알람 범위의 수정·검증·배포는 사용자가 승인했다. 자동 주문, 사용자 계좌 접근, 포지션·레버리지·손실 한도 확대는 범위에 포함하지 않는다. 아직 검증되지 않은 후보를 실전으로 승격하거나 위험 경고를 숨기지 않는다. 다른 방의 최신 작업을 덮어쓰지 않도록 최신 브랜치와 운영 설정을 확인하고 기대 SHA로만 갱신한다. 연구 기록만으로 운영 서비스를 재시작하지 않는다.

도구·실행 시간·자료가 부족하면 실행한 단계와 장애를 기록한다. 실행하지 않은 백테스트·전향 수집·배포를 완료했다고 말하지 않는다. 스케줄 작업은 정해진 간격의 개별 실행이며, 중간에 AI가 계속 작동한다고 설명하지 않는다. 실제 실행 환경에서 계산을 수행할 수 없다면 그 한계를 사용자에게 알린다.

대체 전략의 실제 배포와 적용이 확인된 뒤에만 완료 근거와 롤백 지점을 기록하고 이 반복 작업을 비활성화한다.

## 2026-10-20 추가 진행

- [165개 공개 OBV 구성요소의 실제 평가](lbank_obv_component_validation_2026-10-20.md)는 완전한 월 17개만 포함했다. 2025년 부재·불확실성 하한·비용 두 배 취약성과 알람 계약 부재 때문에 승격하지 않았다.
- [공식 체결 원본 감사](lbank_source_reconciliation_2026-10-20.md)에서 네 날짜 15,212,218행과 공시 SHA를 검사했다. 기존 다섯 불일치 구간의 해제 근거는 확보되지 않았다. 동일 OBV 계산을 다시 하지 않고 경로를 보류했다.
- [공개 곡선 예측 구성요소 규약](../research/continuation/functional_curve_component_protocol_20261020.json)을 먼저 고정하고, [10개 조합의 실제 비용 차감 평가](lbank_functional_curve_validation_2026-10-20.md)를 완료했다. 13개 인과성·공백·비용 검사가 통과했고 예측·손익 장부를 대조했다. 2026년 공통 273일 모두 기본·두 배 비용에서 손실이었고, 모든 조합의 PF와 기대수익 기준이 미달해 탈락했다. 긴 기간의 미상 일자는0으로 처리하지 않았다.
- [추가 원전 6개](../research/continuation/source_screening_20261020_0505.json)는 공개 원전의 비용·빈도·코드·재고 위험 적합성을 심사했다. 새로 백테스트한 결과로 세지 않는다. 다음 단계는 별도의 공개 코드·자료·비용 후 BTC 근거와 고정 손실·TP1·수동 체결에 맞는 원전을 찾는 것이다. 같은 실패 조합의 튜닝·반복 계산으로 돌아가지 않는다. 현재 합격 후보·전향 관측·운영 교체는 없다.
- [외부 EMA 원전의 실행 감사](lbank_external_ema_source_audit_2026-10-20.md)는 원전·규약·코드를 먼저 고정한 뒤 13개 반례와 14개 회귀 검사, 원본 합성자료 생성기의365일 실행을 완료했다. 실제 거래소 자료가 아니었고, 합성79건 중59건의 돌파 전 가격 진입·9건의 설정 재사용·1건의0이하 초기 위험과 ADX 누락을 확인했다. 장부·압축 자산곡선·SHA와 독립 대조를 Git에 보존했다. 실제 시장 손익 검증이나 전략 개선 완료가 아니다.
- [이번 추가 원전 선별](../research/continuation/source_screening_20261020_0558.json)에서 다른 ML 원전의 README 양수 주장과 저장된 손실 보고서의 불일치, 포지셔닝8모델의 기준 미달 빈도를 확인했다. 새 공급·수요 구간 구현을 고정 커밋으로 다음 사전 검사에 넘겼으나, 아직 수익성 근거·구현 적합성을 확인하지 않은 단순 조사 대상이다. 합격 후보·전향 수집·운영 교체는 여전히 없고 기존 기준은 유지한다.
- [공급·수요 원전의 실제 가격 인과성 감사](lbank_supply_demand_source_audit_2026-10-20.md)를 사전 고정 규약·코드와 10개 검사로 완료했다. 이미 관측한 2024년 1월의 완전한 1분봉44,640행을 30분봉1,488개로 집계했고, 첫20일에 미래11일만 추가하자 과거 구간6개·목표3건·이미 종료된 청산 손익2건이 바뀌었다. 양수 진단 합계를 수익성 근거로 쓰지 않고 이 버전을 제외했다. 47진입 중43건의 형성봉 진입도 확인했다. 입력·전체 장부·SHA·별도 검산을 Git에 보존했다. 같은 공급·수요 전략의 매개변수 수정이나 반복 계산은 하지 않는다.
- [추가 공개 원전 선별](../research/continuation/source_screening_20261020_0740.json)에서 `passed` BTC4개 카드의 음수 결과와 별도 세션 추세 코드의 수수료 분모·목표 우선 결함을 확인했다. 새 시장 백테스트로 세지 않는다. 다음은 다른 원전의 공개 스크립트·자료·비용·빈도·입력 조달 적합성을 먼저 확인하고, 가능할 때만 실행 규약을 고정한다. 상태에 구체적 링크와 제외 목록을 남겼다. 합격 후보·전향 수집·배포는 아직 없으며 작업은 후속 단계로 이어간다.
- [공개 WF 논문의 BTC 두 모형 장부 검산](lbank_published_wf_ledger_audit_2026-10-20.md)을 완료했다. 저자 ZIP80.3MB·92파일의 해시를 확인하고, 사전 체크포인트와8검사 뒤 실제 RDS 장부를 계산했다. 평가 창 경계의 비용 누락4건/7건을 보정해도 과거 기본·두 배 모형 비용 합계는 양수였다. 동시에 공통655일의 전환 빈도0.440/0.449건·일, 기회일 약32%, 기대수익 신뢰 하한 음수와 약50~54%의 낙폭을 확인했다. 실전 기준을 통과한 전략이라는 뜻은 아니다. 원본 선택 장부·전체/공통기간 결과·종료 구간·독립 산술 검산을 Git에 보존했으며 원전 변경·재최적화는 하지 않았다.
- [사회 신호 원전 적합성 검사](../research/continuation/social_signal_source_screen_20261020.json)는 광고된 자료·스크립트 URL404와 일 단위/장중 보호 계약 부재를 기록했다. 수익률을 재현했다고 말하지 않는다. 다음은 [공개 native 호가 관측 규약](../research/continuation/native_quote_baseline_protocol_20261020.json)의 한정된12회 기초 관측으로 호가·왕복 지연·누락의 실제 입력 근거를 보완한다. 특정 후보의 전향 검증 시작이나 운영 교체가 아니며, 이후에도 다른 적합한 원전 검색·검증을 이어간다.
- [LBank 공개 호가 실제 관측](lbank_native_quote_baseline_2026-10-20.md)은 08:35:32–08:36:27 KST에 사전 고정 코드·8검사 뒤 실행했다. 12회 중 정상7회·연구 호스트의5초 시간초과5회를 기록했다. 정상 응답의 요청·읽기·파싱 소요 중앙값은0.5806초였지만 호가 생성 시각이 없어7건 모두 최신성 미상이다. 원본12행·본문 SHA·시계·Decimal 호가 폭을 별도 검산하고 Git에 보존했다. 운영 알람 지연 원인·거래소 전체 장애·실제 체결 성과로 단정하지 않는다. 관측은 종료됐고 계속 실행되는 수집기는 없다. 사후 권한 오류 처리 보완 v2의9검사와 실제 v1 관측 코드를 구분해 남겼다.
- [다음 원전의 구체적 체크포인트](../research/continuation/source_screening_20261020_0840.json)는 별도15분봉 LightGBM 구현의 고정 커밋과3개 원문 문서를 연결했다. 저자 성과표는 현재 모델보다 오래됐고 -20% 손절·전체 비용·봉 중 청산·TP1 적합성을 검토하지 못했으므로 수익성 후보로 인정하지 않았다. 다음 단계는 실제 계약 코드·모델 메타데이터·재생/표본 장부의 비용·누출·현재 위험 한도 적합성 검사이며, 학습·최적화·실전 봇 실행부터 하지 않는다. 합격 후보·전향 관측·배포는 여전히 없고 승격 기준과 운영은 변경하지 않았다.
- [15분봉 LightGBM 원전 실행 계약 감사](lbank_lightgbm_source_contract_audit_2026-10-20.md)를 완료했다. 고정12파일의19개 검토 순수 함수만 실행해16검사와10개 별도 산술 대조를 통과했다. 첫 봉 손절은 손실 자체가 총손익에 남지만 수수료 이벤트와 종료 거래 수가0으로 집계됐다. 월말 새 진입·결측 순위의 청산 플래그·손절 재진입 계약 불일치도 반례에서 확인했다. 미래200개를 추가하는 대조군에서는 과거600개 순위·20지표 셀 변경0이었다. 공개 모델/예측/거래 장부가 없어 이 버전을 교체 근거에서 제외했으며 실제 시장 수익성이나 모든 ML의 실패를 판정한 것이 아니다. 원본 URL·SHA·전체 입출력·함수 해시와 별도 검산을 Git에 보존했다.
- 임시 파일을 쓰지 않고 고정URL에서12원본을 새로 복구해 다시 실행했으며 시각을 제외한 모든 진단 필드가 같았다. [추가7원전 사전검사](../research/continuation/source_screening_20261020_1007.json)는 빈도·자산·비용·자료 계약에 따라 기록했고 시장 백테스트 횟수로 세지 않았다. 다음은 [별도 공개 BTC 복합 규칙의 특징량·단위·공개 자료 적합성](../research/continuation/rlxbt_combo_source_preflight_20261020.json)을 먼저 확인하는 유한 단계다. 저자 로컬CSV와 미정의 특징량을 임의로 만들지 않는다. 합격 후보·전향 관측·운영 배포는 없고 위험·승격 기준·운영 서비스는 변경하지 않았다.

## 2026-10-20 10:45 KST 사용자 요청 운영 보완 및 재개 확인 대기

- 사용자의 #13 타이밍 민원에 대해 실제 원본·관측가·판정을 캡처하고 같은 규칙으로 진입가·손절·1차 목표를 재현했다. 저장된 관측가 중 최초 동일 후보는 10:10:43.096, 실제 확인은 10:11:14.574로 약31.48초였다. 그 사이 모든 tick·과거 실제 봉 공개 시각은 없어 더 이른 최초 가능 시각이나 수익은 확정하지 않았다.
- 완료 원본봉의 새 키를 다음 위험 점검에서 심사하도록 분 단위 가격 저장 타이머와 분리했다. 발신 시 최초 돌파 조건과 기존 상위 우선권이 둘 다 사라졌으면 더 기다리지 않고 후보를 취소하는 일관성 제약도 추가했다. 상위 근거가 확실한 작은 되돌림은 기존 우선권으로 허용하며 원래 위험/위치/비용 심사를 새로 우회하지 않는다. #13 차단은 개발 사례이며 독립 성과·대체 전략 승격이 아니다.
- 최종 운영 소스 `862339db2c4f69facc9ba6626e2826d475504174`, 배포 `181eb91b-065c-44b3-bcaf-c58e4cb0fa01`은 SUCCESS. 343구현 테스트, 실제 런타임 소스 해시·정책과 원본 사건 취소 경로가 확인됐다. [코드·재현 문서](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/862339db2c4f69facc9ba6626e2826d475504174/docs/lbank_entry_timing_2026-10-20.md), [배포·운영 체크포인트](../research/continuation/results/entry_timing_hotfix_20261020_deployment.json).
- **진입 운영 재개는 미확인**이다. 재시작 구간 원본1분봉 연속성 공백으로 10:44:56현재 연속1분봉2개이며 기존65개/5분봉20개 및 정렬 문맥 기준을 만족하지 못해 새 진입이 대기한다. 과거5분봉130개라는 숫자로 최신 공백을 무시하지 않는다. 수집 연결과 새1분봉 재축적은 실제 확인됐지만 재개시각은 보장하지 않는다. 공백 보간·준비 기준 완화·연구 문서 저장용 재시작은 하지 않는다.
- 다음 실행은 `state.json.pending_operational_validation`을 먼저 읽어 최신 소스와 native_ready 및 새 완료봉 심사 실행을 실제 로그로 확인한다. 미준비면 상태만 누적하고 중복 경고·재배포 없이 기존 RLXBT 원전 사전검사를 계속한다. 재개가 확인되면 근거를 저장하고 한 번 알리되 대체 연구 자체는 종료/비활성화하지 않는다. 기존 연구 후보·승격 기준과 다음 원전 연구 단계는 그대로 유지한다.

## 2026-10-20 공개 복합 규칙 감사 및 핀바 실제 계산 체크포인트

[복합 규칙 실행 규약 감사](lbank_rlxbt_source_contract_2026-10-20.md)에서 공개 Git92객체·CSV5헤더와 원전 JSON을 고정해 검사했다. 미정의 특징량·원본 자료·장부·실행 우선순위가 없어 이 배포본을 제외했다. 147합성 입력의 충돌과 Decimal 비용 단위를 실제 계산했지만 시장 빈도·성과로 세지 않는다. 원전의 규칙이 완전한 별도1분봉 핀바는 [고정 규약](../research/continuation/pinbar_transfer_protocol_20261020.json)과 실행 코드·11테스트를 과거 손익 계산 전에 고정하고 실제 자료 계산으로 넘긴다. 전 기간을 이미 관측한 선물 전이 검사이며 원전 현물 복제·수익 인증이 아니다. 운영은 재배포하지 않았고 11:23:48KST 준비수는1분41개/5분7개로 실제 재개는 아직 미확인이다.

## 2026-10-20 11:27 KST 핀바 실제 과거자료 계산 완료

[공개 핀바 원전 고정 규칙 전이 검사](lbank_pinbar_transfer_validation_2026-10-20.md)를 실제1분봉1,445,760행·펀딩3,012행으로 완료했다. 66원본ZIP SHA를 기존Gitmanifest와 대조했고25분격리를 유지했다. 규약·코드를 먼저Git에 고정하고, 직렬화 형식 오류만 고친 실행 체크포인트도 저장한 뒤 보존 실행했다.12구현검사가 통과했고26,128회 별도Decimal현금 검산·실제1월종료589개중복시나리오행의 미래추가 변경0건을 확인했다.

즉시모형6,737종료거래는PF0.4705·목표선도달23.82%·평균순-0.4604R,추가1분모형6,327건은PF0.4939·목표23.98%·평균순-0.4270R였다.2024/2025/2026년1~9월 각기 기본·두배비용 모두음수다. 중복두모형을 독립표본으로 합치지 않는다. 전체압축장부·원본URL/SHA·규약·산술검산을Git에 보존했다. 초기손절분봉비율은실제긴급경고율이아니고,이미관측한선물검사를원전현물복제나LBank실전성과로 바꾸지 않는다. 이버전제외는모든핀바기법무효판정이아니다.

[다음원전선별](../research/continuation/source_screening_20261020_1132.json)은기존제외목록중복을먼저대조하고,별도CShear공개원전의고정커밋·읽을4파일·BTC단독장부/전체비용/누출/공백검사순서를상태에남겼다. 포트폴리오양수를BTC성과로옮기거나63전략최적화를시작하지않는다. 합격후보·전향검증·대체운영배포는아직없으며작업은다음유한단계로이어간다. 운영은재시작하지않았다.11:30:06KST실제준비수48개1분봉/9개5분봉으로기존준비계약을기다리는상태만갱신했다.

## 2026-10-20 12:18 KST 공개 CShear 실행 계약 감사 완료

[원전 감사](lbank_cshear_source_audit_2026-10-20.md)는 고정 커밋의84Git객체와8개 검토 정의를 검사하고13개 합성 회귀 검사, 실제BTC2024년1월44,640분의185개 비정시 조회 대조를 완료했다. 미래 봉 추가 후185개 과거 조회가 모두 바뀌었고, 정확한 라벨185개 대조군은 변경0개였다. 같은 과거의 펀딩 숏 방향도 미래 가격에 따라 청산 비교에서 롱/숏으로 달리 복원되는 반례, 자료끝48시간요청→4시간조기청산·시간공백·손절갭·봉내추적순서 문제를 재현했다. 별도 원본 분봉·파일SHA·Decimal 산술 검산이 통과했다. 모든 원전 정시 성과의 누출이나 양수 수익률의 왜곡 규모를 계산한 것은 아니다. 공개트리에 원본BTC장부가 없고 전체비용·TP1·긴급경고·native체결·전향 기준이 미검증이므로 이 배포본을 제외했다. 전략군 전체를 무효라 하지 않으며 같은 버전의 수정·최적화로 돌아가지 않는다.

[추가 원전 선별](../research/continuation/source_screening_20261020_1224.json)은 세 새 공개README를 고정SHA로 대조했다. 새 GitHub 원전의 BTC단독 장부·원본 입력·수수료/미끄러짐/펀딩 현금흐름을 다음 유한 단계로 남겼다. 저자 타자산 양수나 lookahead-free 문구를 BTC 수동 알람의 증명으로 쓰지 않는다. 합격 대체 후보·전향 수집·대체 전략 배포는 아직 없다.

## 2026-10-20 12:25 KST 기존 타이밍 보완의 진입 심사 재개 확인

[실제 운영 로그](../research/continuation/results/operational_readiness_20261020_1225.json)에서 운영 소스862339d/배포181eb91b SUCCESS 상태의 연속1분봉103개·5분봉20개와 native_ready/entry_source_switched=true를 확인했다.12:25:05에 native_completed_bar_schedule_v1의 new_completed_bar 심사도 실행됐으며 후보0·market_no_setup이었다. 실제 원본 봉 공개시각이나 거래소→알람 전체 지연은 측정하지 않았고 새 후보 확인·발신도 이 관측에서 없었다. RSI250봉 성숙표시는 아직false지만 기존 feed준비 계약은 통과했다. 문서 저장용 재시작·준비기준 완화·위험/증거금 확대는 없었다. 기존 타이밍 보완의 재개 확인이며 대체 전략 수익성·운영 교체 완료가 아니다. 재개는 한 번 알리고 후속 연구를 계속한다.

## 2026-10-20 12:40 KST 펀딩·체결흐름 공개 원전의 BTC 계약 감사

[Gotodataru 원전 감사](lbank_gotodataru_source_audit_2026-10-20.md)는 고정51파일·14개 검토 정의를 검사하고11개 합성 회귀 검사와75개 별도 Git객체/요약 산술 검사를 완료했다. 공개된 CSV3개는 종목별 요약이며 원본BTC 종료 장부·입력Parquet·창별 선택 매개변수는 없다. 저자BTC139/318/368종료 요약의PF는 각각0.6183/0.3322/0.7111로 음수다. 소수 수익률을 백분율로 표시한100배 단위 문제, 펀딩 정산 누락, 생성자 비용 변경 무반영, 자료 끝 조기 청산·시간 공백·손절 갭 및 목표 슬립을 고정 입력으로 재현했다. 올바른 다음 시가·손절 우선·49개 과거 신호 prefix 불변 대조군도 보존했다. 시장 수익성 백테스트·저자 성과 재현·전체 왜곡 규모를 계산한 것은 아니다. 실제 빈도/기회일·전체비용2배·불확실성·TP1/긴급 경고·native 검증은null이며 이 배포본만 채택 근거에서 제외한다.

[추가 원전 선별](../research/continuation/source_screening_20261020_1240.json)에서는 pgup 상태모형과maxs LSOB의 저자 양수를 남기되 낮은 빈도·미공개장부·현재 수동매매 계약 차이를 표시했다. GT의 목표58~65%승률은 실측이 아니므로 후보로 세지 않았다. 기존Bysik논문은 중복 목록에 돌려놓고 재훈련/재계산하지 않았다. 다음 유한 단계는 새 고정 원전의 실제BTC 장부·원본 자료 존재를 먼저 확인하는 것이며, 예제 출력을 실측으로 세거나 빠진 특징량을 만들지 않는다. 장부가 있으면 먼저 날짜별 기회·비용을 실제 계산한다.

[운영 연속성](../research/continuation/results/operational_continuity_20261020_1230.json)은 기존862339d/배포181eb91b SUCCESS와 준비·새 완료봉 심사 유지만 확인했다. 기존 재개 알림을 반복하지 않았고 연구 저장용 재배포도 없다. 합격 대체 후보·전향 수집 시작·운영 교체는 아직 없으며 승격 기준과 위험 상한은 그대로다.

## 2026-10-20 14:23 KST 공개 장부 원전 2건 확인 및 실제 가격 시점·현금 감사

[새 원전 검사](lbank_public_ledger_sources_audit_2026-10-20.md)를 완료했다. 첫 원전15391f의45거래/PF1.85는README예제이며25파일에원본BTC가격/종료장부가없었다. 인증클라이언트·공개문서의인증정보를사용/복사하지않았다. 두번째dcd1ac3의39파일·검토21정의에서는 실제재사용BTC2024Q1분봉131,040개로420개과거특징량/31개당일조회와합성현금·집계계약을실행했다. 규약e63fe59를먼저고정하고,CSV헤더입력오류는673c06e에수정기록후재실행했다.10회귀검사·125별도Git/SHA/Decimal/실제가격검사가통과했다.

미래가격추가후과거highvol분류26/420개가변했고당일일봉ADX24/31·대체EMA31/31값이바뀌었다. 그러나전일상승조건과31개상승/하락boolean은변경0이며EMA/ATR/RSI가격대조군도동일했다.26개거래오류나전체수익왜곡으로일반화하지않았다. 종료포지션1개손실인합성사례의부분익절/손절사건승률50%,진입수수료추가차감0.040008,숏의유리한슬립·종료잔고/경로불일치도재현했다. 수수료오류가해당사례손익을낮춘다는방향도보존했다. 저자최종OOS양수+2.3827%는1진입·1강제청산·PFNaN인원전요약으로만남겼으며실측자료/합성출처를확정하지않았다. 원전실제시장수익성백테스트는아니고전체비용·불확실성·TP1·긴급·native기준은미검증이다.

[다음논문원전](../research/continuation/source_screening_20261020_1423.json)은고정5693993의주야간세션코드·실제1050/1800행원전표가있으나Kraken원자료는미공개라고명시한다. 다음유한단계는정확한비용식·시점·전체BTC표·공식자료출처정합성대조이며최고컷오프수익만채택하지않는다. 다른거래소자료를Kraken으로바꾸지않고25전략/12컷오프재최적화나임의손절/목표를만들지않는다. 합격후보·전향수집·대체배포는없고,기존운영862339d의준비/심사유지만읽기확인했다. 연구저장용재시작·위험상한변경은없다.

## 2026-10-20 공개 주야간 논문 BTC 전체 표 비용 산술 완료

[원전 비용식·자료 출처 감사](lbank_daynight_cost_audit_2026-10-20.md)는 규약/코드 defd038을 먼저 고정하고 실제 BTC900행·핵심525행 중복·300조합을 계산했다. 첫 실행의 미사용0지수 로그 수치 오류만 고친 뒤9구현검사·12합성현금 대조·3,307별도Decimal/SHA 검사를 통과했다. 비용식 최대 상대오차3.34e-13, 핵심표 중복오차0이다. 원본 가격 재생이나 새 시장 수익성 백테스트로 세지 않는다.

저자가 전체 표본에서 선택한08UTC Reversal/Reversal의 시작100 대비 조건부8bp 최종값237.2228과16bp0.5866을 모두 보존했다. 두 배 진단에서도 양수인12조합은 모두 장기롱의 시간 경계만 달리한 포지션변화1회 사례여서12개 독립우위/일일 알람으로 세지 않는다. 전체300·900행 대조와원전추론BTC9행·SHA·명령을 저장했다. 원전의Kraken CSV/SHA와 종료장부·초기손절/TP1·전체비용·native수동체결·전향근거는 미확정이고, 원문BH대비신뢰구간을 절대순기대R로 바꾸지 않았다. 원문규칙에 없는 출구·펀딩을 만들어 합격시키지 않는다.

[다음유한체크포인트](../research/continuation/source_screening_20261020_1456.json)는 별도원전Silentbilly의 고정2227152·36파일과 빈자료/미공개결과를 확인하고 원본신호·위험·다음시가·보호활성화·비용계약 검사 후 적합시 단일설정/한정된실제자료 계산을 남겼다. 미검증자료나 결과를 만들지 않고, 이미제외한후보재최적화를 하지 않는다. mantasbil의90파일/README는XAUUSD연구여서BTC장부로쓰지않는다. 운영862339d·배포181eb91b SUCCESS와14:45:19KST기존준비/심사유지만 읽기 확인했고 재시작은없다. 합격후보·전향수집·대체전략배포는 아직없으며 작업은 후속 단계로 이어간다.

## 2026-10-20 16:13 KST 원전 실제 모의 엔진·진입 보호 검사 완료

[Silentbilly 원전 계약 검사](lbank_pullback_contract_audit_2026-10-20.md)는 규약/코드 c55bc6c를 먼저 고정하고, 원전22AST정의·10파일을 실제Backtrader1.9.78.123 메모리 내 엔진에서 실행했다. 10고정합성가격 사례에서 진입봉 손절접촉 미청산, 목표/손절 이중체결 뒤 반대포지션 잔존, Margin 뒤 pending미정리, 종료분석기 수량0·종료가격=진입가격을 재현했다. 손절만/목표만/불리한 손절갭·수수료두배 정상 대조도 보존했다. 종료손익숫자 자체는 정상 대조에서 맞았으며, 반대 미청산 포지션을 빼놓은 종료장부를 계정 전체수익으로 바꾸지 않았다. 기존LBank운영의 결함이나 실제오류빈도로 일반화하지 않는다.

실제 재사용BTC2024년1월44,640분의 ZIP/CSV SHA·연속성·OHLCV를 검사하고2,976개의완전15분봉으로 집계했다. 첫20일1,920봉의44출력열84,480셀은 미래11일추가후 변경0이었다. 이 대조에서 신호누출은 찾지못했다. 롱16/숏11·기회17/31일은 원시신호일 뿐 실제알람/체결성과가 아니다. 실제sample은ETH현물이므로 BTC양수로 전용하지 않았다. 15회귀검사와44,784별도검산(그중44,639분봉연속성)이 통과했고 전체주문·잔고·원시신호·SHA·재현명령을 저장했다. 시장수익성백테스트·순기대/PF/TP1/긴급/native게이트는 미측정이다. 이 버전만 제외하며 재최적화하지 않는다.

[새 공개원전 체크포인트](../research/continuation/source_screening_20261020_1619.json)는 shivanshu1177의 고정776003e·297파일 중 실제기본요약JSON과 버전관리된BTC가격/펀딩/Parquet장부경로를 확인했다. 저자기본OOS +0.1108%/58거래·IS−9.3%와 낮은빈도 우려를 함께 남겼고, 거래사건과 종료포지션 단위·현금/비용·기회일을 원본장부로 확인하는 유한단계를 다음으로 지정했다. 81설정 탐색·Kelly/레버리지 확대·낮은수수료로 합격시키기는 하지 않는다. 별도buffettlaw는ETH전용leadlag/BTC+ETH포트폴리오·10x제약이라 BTC단독수동우위로 쓰지않았다. 운영862339d/배포181eb91b SUCCESS는 읽기확인했고 재시작은없다. 합격후보·전향수집·대체배포는 아직없으며 후속 기록을 이어간다.

## 2026-10-20 16:48 KST 원전 실제 공개 BTC 기본 장부 현금·비용·빈도 계산

[Funding Harvester 장부 검산](lbank_funding_ledger_audit_2026-10-20.md)은 원전776003e와 계산 규약/코드d91f212를 먼저 고정하고25입력의 전체SHA/Git객체를 확인했다. 원문기본IS234체결→117왕복, OOS58체결→29왕복을 구성해 원문펀딩·양쪽실제명목금액 수수료·체결가내 슬립을 현금으로 재계산했다. 기본IS−1085.796970/OOS+11.079637 USD의 양수/음수를 모두 보존했다. README−9.3%는1월시작다른경로이며 이번registry에맞춘3월시작IS는−10.858%다. 12회귀검사와1278별도Decimal/Git/SHA 대조가 통과했고 최대현금오차1.70e−11 USD였다. 새로운 원문신호 시장백테스트나 native수익으로 세지 않는다.

OOS PF1.014094, 진입29/213일·기회28/213일, 고정장부에서 수수료/슬립/펀딩지급 두배 순−193.888375 USD였다. 수취는 증폭하지 않았다.7/30일블록각5000회 조건부USD평균의95%하한은−21.3233/−20.2599로양수가아니다. R/TP1/5분긴급/native체결/전향게이트는미측정이다. 종료강제청산·공백거래는이번두장부에서0건이므로 작은양수를임의끝청산탓으로하지않는다. 원문함수의 추가슬립차감·펀딩누락은OOS통계값을−30.3176 USD낮추지만, 계정말단+11.0796을가짜로만드는오류는아니다. source시간봉평가경로는다음시가체결을한시간앞에기록하고 registryMD5는첫100행/길이만해시한다는한계도보존했다.

[다음원전/접근체크포인트](../research/continuation/source_screening_20261020_1657.json)는 기존Pavdot/3669기술규칙중복을새검증으로세지않았고 새regime원전의현물/최소42시간보유·공개입력/장부부재를기록했다. 새MACD 원전08e5f1a의12객체트리는확인했으나 README다운로드가CONNECT403으로차단돼그접근은중단하고우회하지않았다. arxiv2201.04699v4직접열기도DisabledError라원문전체검토로세지않는다. 다음유한단계는UCL DOI10.1109/ACCESS.2022.3166599 원저자논문의정확규칙·공개코드/자료/장부·수동BTCUSDT적합성확인이다. 초록순수익350%는저자주장일뿐이며공개자료가없으면임의훈련/TP1/사적접근없이장애를기록하고다른원전으로넘긴다. 현재연구전체가차단됐다고확인된것은아니다. 운영862339d/181eb91b SUCCESS·native358분봉/71개5분봉과기존새봉심사유지만읽기확인했고 재시작/교체/위험확대는없다.


## 2026-10-20 17:54 KST 사용자 우선순위: 방향 근거와 진입 타점의 원전 비교

사용자는 볼린저밴드를 단일 해법으로 삼으라는 뜻이 아니라 공개된 진입 정보를 취합해 우리 수동 알람의 방향·위치·발동 원칙을 확립하라고 명확히 했다. [진입 설계 원칙 v1](lbank_entry_principles_2026-10-20.md)에 공식 Bollinger Method II 2021 설명서, Connors 2009 저자 일봉 ETF 규칙, Wyckoff Analytics 2020 저자 LINK 스윙 사례, CME 공식 지지저항 교육을 비교했다. 서로 다른 자산·시간봉·판본을 합치거나 교육 자료를 비용 후 BTC 우위로 세지 않았다. 특히 MFI14 기본과 %MoneyFlow 정규화10을 서적판 MFI10 규칙과 혼동하지 않고, Connors의 일봉을 5분봉으로 임의 치환하지 않았다. 원전 코드/장부 또는 초기손절·TP1이 누락된 항목도 표시했다.

기존 성과 채택 기준은 존재하지만 기법별 적용 조건·방향 가설·진입 위치·발동·무효화·목표를 일관되게 연결하는 설계와 운영 검증은 미완료다. 1분 EMA/MACD 후보와 별도 5분/15분 상위 근거 경로를 소스 수준에서 구분했다. 새 지표 점수·무조건 정렬·밴드 역진입·추가 대기·위험 경고 은폐를 도입하지 않았다. 17:34 발신/경고 기록과 17:38 완료봉 밴드 위치 재현은 상태의 필수 읽기 목록에 연결했고 오늘 사례를 독립 검증으로 세지 않는다.

다음 유한 작업은 원전별 완전한 진입 계약 비교표와 보존 #8~15 전체의 개발 진단이다. 숫자가 없는 원문을 임의로 채워 합격 후보로 만들지 않으며, 완전한 새 BTC 규칙만 고정 계산으로 넘긴다. 4자료 비교/원칙 문서화는 완료했으나 새 수익성 백테스트·합격 후보·전향 시작·운영 교체/배포는 0건이다. 상태에 이 작업의 우선순위를 남겼고 기존 연구 결과·승격 기준·운영 서비스는 변경하지 않았다.


## 2026-10-20 17:58 KST 연구 방향과 산출물 우선순위 변경

사용자는 연구·검증의 양에 비해 기본 진입 원칙이 완성되지 않은 점을 연구 방향의 문제로 지적했다. 외부 소스 오류 감사를 쌓는 것과 현재 수동 알람의 방향/타점 우위를 개선하는 것은 다른 성과임을 인정하고, state.json의 **실제 next_work.stage와 instruction**을 기존 알람의 원인 귀속 우선으로 변경했다. 직전 원전 검색 단계·지시·후보 링크는 source_search_resume 및 기존 sources_to_screen에 보존했다. 새 문서만 만든 것을 개선 완료로 세지 않는다.

첫 유한 산출물은 보존 #8~15 전수의 정책 버전·시점별 방향 근거·가격 위치·발동·무효화·TP1·경고·누락 비교표와 원래 통과 경로의 재현 가능한 설명이다. #14 되돌림에서 시작하며 이미 계산한 밴드/로그 결과를 반복 계산하지 않는다. 최초 가능 진입과 반사실 수익은 원래 전 관측이 없으면 미확정으로 남긴다. 이로부터 고정한 개선 가설을 정하고, 외부 검색은 그 계약의 명시된 공백을 채우도록 한다. 기존 탈락 결과 재활용·비용/손실/승격 기준 완화·위험 경고 은폐·검증 없는 운영 변경은 하지 않는다. 이번 변경은 연구 순서와 산출물의 변경이며 수익성 있는 공식 완성이나 운영 성능 개선을 뜻하지 않는다.


## 2026-10-20 사용자 신속 산출물 요청: 실제 통과 경로 구성요소 재현·보고 정책 변경

[실제 실행 결과](lbank_entry_direction_location_audit_2026-10-20.md)를 저장했다. 보존 #8~15 전수8행과 #14 실제12:35거부/12:37통과 로그·20완료5분봉·현행소스blob을 입력으로 고정했다. 원래피벗/목표공간2정의를 AST추출하여 동일봉·동일목표에서81971→82011.8→82014 가격만변경하면 conflict→unknown→unknown이 되는 경로를 실제재현했다. 이전저항81987은현재가앞에없어져 제외되며 마지막완료종가81974.1은저항아래였다. 목표가저항앞에있는정상대조clear와Decimal가격순서·Gitblob·20봉연속성도확인했다. 1분입력·목표·다른조건도원래시간간변했으므로 전체통과의유일원인으로단정하지않는다. 전체원래1분스냅샷·5분창·공개시각은미확보이며전체classify재현·새성과계산·새차단규칙·운영수정/배포는없다. 기존성과재계산/탈락반복으로세지않는다.

사용자요청에따라 반복작업6ad658e93c208191bd473ceb228e15b8의 실제prompt를수정하고peek재조회로확인했다. 상세중간연구/탈락은GitHub에만기록하고 실제운영변경/배포·중요성능단계전환·사용자조치필요장애만최대4문장으로알린다. 활성상태·실행빈도는유지했다. 다음단계는기존원본스냅샷의승인된읽기접근가능성1회확인 또는무진입변경event-time증거capture패치와명시된장벽전이계약의고정비교다. SSH/CLI가없다는현재장애로서비스재시작/위험확대/검증기준완화를하지않는다. 완성된실전알람을제공하거나수익성개선을확인한상태는아니다.


## 2026-10-20 무발송 가격 재생 검증 완료 및 후속 검사 전환

[실행 결과·한계](lbank_offline_entry_comparison_2026-10-20.md)를 기록했다. 결과 전 규약/코드/범위 6cc4483/63f2720/bc62690을 고정하고 재사용 Binance 2024-01-08~15 UTC 분봉을 실제 순서 재생했다. 초기 손절/TP1 최초 사건 진단 기준 기존17신호16종료 -2.6894R/PF0.7285, 상위 방향 필수안16신호15종료 -4.1457R/PF0.5815이며 비용 두 배도 각각 -7.5966R/-8.8193R이다. 종료 각1건은 자료끝 미종료로 남겼다. 중복되는 두 안을 독립31거래로 세지 않으며 이 한 주 손실로 전략군 전체를 무효화하지 않는다. 현행 수익 보호/부분익절은 포함하지 않아 실제 전체 운영손익·긴급 경고율은 미검증이다.

별도62 Decimal비용검산과16과거시점 미래봉변경 대조를 통과했고 평균R 조건부 2일블록95%구간은 양수하한을 충족하지 않았다. 실제 알람/주문0건이며 native LBank 성과나 새 독립/전향자료가 아니다. 따라서 해당 방향필터의 운영 적용은 하지 않는다. 빠른 과거 재생을 먼저 진행하고 native 현장검증은 별도 유지하는 사용자 요구를 실제 계산으로 반영했다.

기존 입력 접근은 도구/CLI 유한 확인 후 종료했다. opt-in 비차단 사건시점 공개입력 capture 패치를 별도 보존하고 실패/큐초과가 진입/경고에 영향을 주지 않는지 포함349코드검사를 통과했다. 기본false, 미배포/미수집이며 성능개선으로 세지 않는다. 운영862339d/181eb91b SUCCESS와18:37:44KST native360분봉/94개5분봉 준비를 읽기확인했고 운영을 재시작하지 않았다.

다음 실행은 같은 주간의 반복/재최적화 대신 구간을 먼저 고정하여 잔여2024-01과 다른 연도/국면을 동일규칙·전체비용으로 비교하거나 실제 기존 수익보호/부분익절 경로를 고정해 재생한다. 긴 대기만을 검증으로 삼지 않되 기존 승격 숫자를 낮추거나 과거 모형을 native실적으로 바꾸지 않는다. 합격후보·전향시작·운영교체/대체배포는 아직 없다.


## 2026-10-20 19:23 KST 검증용 모의 알림 배포·실제 실행 확인

사용자는 잦은 모의 알림과 개별 요청 없는 최종 결과까지의 후속 진행을 승인했다. [모의 알림 기능](lbank_paper_notifications_2026-10-20.md)을 기존 공개기법4종/RSI 연구 장부의 별도 read-only daemon으로 구현했다. 후보/모의 진입/TP1 상태/종료는 서로 다른 사건으로 구분해 최대1분당4사건1메시지로 묶고 15분 상태 메시지는 거래 수로 세지 않는다. 기존 실전 게이트/증거금/손실/위험 경고·연구 신호 규칙은 변경하지 않았고 주문0이다.

최초 업로드가 출력 토큰 제한으로 잘려 서버 predeploy에서 구문 오류가 차단됐다. 전체 원본을 재구성하고 변경3파일의 remote Gitblob=local을 확인했다. 이후 추가한 불변성 검사의 Python AST 판본 차이1건을 원문 함수 해시 대조로 수정했다. 두 실패 배포는 새 런타임을 가동하지 않았고 기존181eb91b 원본360/102 준비가10:18:54Z까지 정상임을 확인했다. 오류와 수정은 상태에 보존했다.

최종 운영5c977a7 / 배포23799b11-6f14-4e93-aacf-78ce691f9393 **SUCCESS**. 로컬/서버350검사가 통과했고 런타임 소스SHA256 4f8928f2…가 로컬과 일치했다.19:22:15KST PAPER_NOTIFICATIONS_STARTED와19:22:16 첫progress delivered=True(API성공응답),19:23:07 원본360분봉/103개5분봉 native_ready·기존 두 연구 ready=true를 확인했다. 사용자 기기 수신은 별도 미확인이다. 현재 관측창 후보/모의진입/종료0이며 시간봉연구ready=false·RSI250 성숙false를 유지한다. [선별 원본 로그·실행 증거](../research/continuation/results/paper_notifications_20261020/runtime.json)를 저장했다.

이는 모의 알림 전송과 수집의 실제 실행 확인이지 수익성 개선/합격 후보/전향 채택/대체 전략 운영 교체가 아니다. 기존 반복 작업6ad658e93c208191bd473ceb228e15b8의 추가 지시와 활성 상태를 재조회했다. 다음 실제 후보/종료의 원본 관측·공백·비용 결과를 기록하면서 기존 고정 오프라인/운영 출구 재생 검증을 이어간다. 개별 재요청을 기다리지 않으며 달력 대기만으로 채택하지 않는다.


## 2026-10-20 19:45 KST 기존 진입의 추가24일 고정 구간 재생·검산 완료

[추가 구간 실제 결과](lbank_entry_regime_extension_2026-10-20.md)를 저장했다. 규약19f7b645/계산84fa6133을 결과 전에 고정하고 이전7일을 반복하지 않았다. 2024-01-15~02-01 UTC17일과2025-01-08~15 UTC7일을 같은 초기손절/TP1 최초 사건 모형·같은 비용·동일 두 규칙으로 실제 재생했다. 2024 기존59종료 -20.8714R/PF0.5085, 상위방향필수57종료 -18.8051R/PF0.5345이며 비용두배 -40.3556/-37.6619R. 2025 기존22종료 -1.7580R/PF0.8681, 필수21종료 -5.6610R/PF0.6055이며 두배 -8.6550/-12.2633R이다. 끝/공백 임의청산0, 두 안의159행은 겹치는 참조로 독립159거래가 아니다.

2024 필수안의 상대개선과2025 악화를 함께 보존했다. 사후기술집계에서2025 기존추세되돌림숏13종료 +4.4781R와 같은2024셀22종료 -9.0741R 모두 기록하고 좋은 셀만 채택하지 않았다. 필터가 실전쿨다운 기회까지 바꾸므로 단순 부분집합 비교로 세지 않는다. 각월44,640실제분봉·공식원본ZIP SHA/기존manifest·OHLC·누락0·펀딩93행 정합을 확인했다.318Decimal비용검산 최대8.82e-14R, 고정00/12시48개 과거입력·classify 미래HLC변경 대조가 통과했다. 2/7UTC일5000블록 불확실성을 남기고7일자료의7일블록은 퇴화하므로null로 표시했다. 원격/로컬결과blob동일도 확인했다.

이미본다른거래소개발자료이며 정확한15/30초·호가/수동체결·전체미래추가·계정MTM낙폭·현행수익보호/부분익절/러너 전체는 재현하지 않았다. 실제LBank5분긴급경고율null, 새합격후보/전향채택/운영교체없음. 이번 추가계산으로 상위방향필터를 배포하거나 모든기법이무효라고 일반화하지 않는다. 다음 유한단계는 실제 기존 수익보호/부분익절/러너/경고의 함수·호출순서를 고정하고 결정적 가격사례 검산 뒤 동일진입 한 구간의 관리경로를 실행하는 것이다. 초기손절모형 동일구간 재계산·사후튜닝은 하지 않는다.

기존paper운영5c977a7/23799b11 SUCCESS를 읽기확인했다.19:23:08~19:45:30KST를 두개비상한로그창97/115행으로 조회했고 후보/진입/종료0,19:30/19:45 progress API응답2건은 거래로세지 않았다. 원본360/108 native준비 유지, hourly연구미준비/RSI250미성숙은 그대로다. 변경없는상태/탈락중간보고는사용자에게반복하지않고 상세를GitHub에남긴다. 운영수정·재시작·위험확대는없다.


## 2026-10-20 20:28 KST 고정 22개 진입 기존 수익보호·분할익절 실제함수 재생 완료

[관리 경로 재생](lbank_full_management_replay_2026-10-20.md)은 규약d51dc7b0/코드8fc91269를 시장결과전에 고정했다. 무공백 러너 합성사례에 중간관측290초가 빠져 원래공백검열이 정확히 작동한 첫실행은 시장재생전에 중단됐고, d78443f9에서 합성분관측만추가후13고정사례를 통과했다. 운영원문6파일blob과SHA를 대조하고 기존2025년고정22진입의시각/방향/기법/가격/손절/TP1을 모두일치시켰다. 종전장부에없던TP2/구조근거/계획을 원래후보확인경로에서 복원한 것은 새로운관리종착점을 위한 메타데이터작업이며 최초사건손익반복으로세지않는다.

기존PerformanceBook.observe→SignalRiskTracker.check→가상전달성공/주의·무효경고기록→ack→신규고정진입등록순서를 2025-01-08~15 UTC, 후속01-16까지실행했다. levels22완료 -8.5057R/PF0.5377/두배 -15.4097R, warning_exit22완료 -7.5085R/PF0.5685/두배 -14.4124R. 현재관리trend_runner는9완료/13검열이며9건조건부 -8.5660R/PF0.1038/두배 -11.3345R다. 검열은봉내손절4/봉내보호선9건이고22건전체기대손익은null이다.66행은동일22개중복참조이며검열을0/승리/확정손실로대체하지않았다. 이전최초사건 -1.7580R와 차이를 수익보호효과로빼지않는다.

원래장부USDT/예비펀딩과 별도로각분할청산의진입·청산슬립/수수료·실제Binance펀딩/두배를 계산했다.132Decimal비용+53원래장부현금검산 최대1.18e-13R/1.66e-13USDT,22개과거경계에서기존관리한단계미래HLC불변을통과했다.2일UTC블록5000조건부불확실성·TP1확인상태·가상경고를남기되실제LBank5분경고율null을유지했다. 이미본다른거래소개발자료/분관측/전달성공가정이며실제15초·수동체결·전체미래추가·계정MTM은미검증이다.전체원문계획/66행/출구다리/경고compactJSON의원격blob131d43474b39450f518b97233377e2c06556d4d8를검산했다.

다음유한단계는같은22진입의명시적O-H-L-C/O-L-H-C15초가정민감도다. 기존분표본주기는유지하고합성15초를원래5분표본으로잘못세지않는다.가정경로를실제tick/수동체결/전체가능손익상하한으로표현하지않고원래검열은별도보존한다. 새출구숫자맞추기·낙관체결로승격·반복최초사건계산은없다. 유의한우위가없으면원래진입방향/위치의구체적공백으로돌아간다.

운영5c977a7/23799b11SUCCESS읽기확인.20:00~20:28:30KST연구로그60행후보/진입/종료0,20:00/20:15progress API성공2건은0거래다.원본360/116준비·시간봉연구미준비/RSI250미성숙유지.이번실행은운영변경/재배포/위험확대없고합격후보/채택전향시작없다. 상세중간결과는GitHub에만저장한다.


## 2026-10-20 22:19 KST — fixed22 hypothetical intraminute sensitivity
Completed pre-frozen two-path15second replay using unchanged source5c977a7, fixed22plans, original minute sample cadence and verified official Jan2025 archives;13golden cases,92160completed-bar prefix assertions and264Decimal cash comparisons passed. Both paths runner-6.062594R/PF.678123/doublecost-12.962485R; levels/warning-5.129937R. Hypothetical paths only, same22paired entries, not44independenttrades; original13unknowns preserved, true native all22return remains unknown. No promotion/operational change. Scratch cleared and fully restored via32sourceblob manifest; result remote blob verified. Source HEAD fresh, runtime not freshly verified this run. Details: [path sensitivity](lbank_intrabar_path_sensitivity_2026-10-20.md). Next actual task: fixed22 known-at-entry direction/location/target-room acceptance inventory, reuse crossed-pivot attribution and freeze only a justified source-grounded correction, no further exit variants.


## 2026-10-20 22:32 KST — exact22 context paths; input correction
IMPORTANT prior22:19intrabar result invalid for active native management: quality field omitted,valid_state false. Corrected/pre-frozen input+readiness assertions rerun; runner hypotheticalHL-7.037266R/LH-7.433942R, bothnegative. Exact original22ranked numeric/room matches and22future-prefix checks:13conflicts all overridden,9unknown,0clear;3quote-only crossed pivots. No source/risk/operating change or promotion. Frozen next single hypothesis target_conflict_no_direction_override_v1 rejects knownconflict only, all other contracts unchanged; paired descriptive screen next, no posthoc threshold fitting. Actual original13unknowns preserved. Runtime23799b11SUCCESS/source5c977 native360/141, capped101rows22:22–22:31KST show1unique waiting/rejected studycandidate,0newopens/closes,1progressAPIack≠trade. [Details and reproducible results](lbank_fixed22_entry_attribution_2026-10-20.md).


## 2026-10-20 23:33 KST — frozen conflict-veto paired screen
Actual selected22subset calculation complete:9retained/13removed. Levels retained+1.128132R/PF1.231731 but doublecost-1.698265R;runner-3.673576/-1.787494R under two hypotheticalpaths. Removed profitable exits preserved,7retained+6removed originalrunnerunknowns retained. No promotion or deployment; paired7dayblock intervals are descriptive and notpositive strategy expectancy evidence. Exact threeinputSHA/assertions and resultremote blob verified. Next bounded calculation reuses existing public-techniques4 actual source episodes/defaultgates, freezes cadence/complete input schema and checks source-ready fixtures before outcomes; no new technique or threshold tuning. Targeted primaryBollinger explanation read, notnumericcompletecandidate. Existing23799b11SUCCESS/source5c977;2progressAPIacks nottrades and fullstudyledger unqueried. [Full results](lbank_target_conflict_screen_2026-10-20.md).


## 2026-10-21 00:23 KST — user-requested resistance/pressure work
Deployed ONLY descriptive observer25388b4/deploy4bbda6c7SUCCESS; exact runtimehash/native360/163/new readyobservations verified.355tests locally/server;24non-run defs unchanged. Pivotcrossing retained, repeatedpivotreaction/ATR and priceprogress/totalvolume recorded perclosed5m, no fittedscore, no entryoverride. **Requested live decision correction remains incomplete; this is not profitability improvement or completed replacement.** Evidence and next actual missed-rally input/control comparison in [deployment scope](lbank_resistance_pressure_2026-10-21.md). Prior public4 episode task preserved but subordinate to user's specific missed-rally defect. No risk changes;rollback5c977a7/deploy23799b11.


## 2026-10-21 01:14 KST — existing4 actual chronological episode replay
Recovered14/14startup archivechunks, wholeSHA e31309e...verified;#15nativearchive ends07:44UTC and doesnotcover13:40UTC missedrally. NoCLI/SSH/filetool; unknown preserved, no restart. Following fallback executed pre-frozen unchangedpublic4 Jan2025entryJan8-15+Jan16followup:11520polls,108uniqueevents,2boxopens,levels-1.703169R/doublecost-2.372083,runner-2.168884;other3zeroentries.11sourcefixtures+12Decimalcash checks, resultremoteGitblob642a15a320adc986c91edac808315c6f058f1dab verified. Minuteopen transfer, reuseddevelopment/Binance notnative, emergency/manualfillsNULL. This finite test ended; no promotion/deployment. Next one contrasting2024sameepoch same-rules/dateonly replay, dataSHAfreeze before outcomes, no calendar sweep/tuning. Latestuser wants existing referencealerts kept and valuable evidence-supported interimchanges; recorded. [Full evidence](lbank_public4_chronological_replay_2026-10-21.md).


## 2026-10-21 01:38 KST — contrasting replay frozen; compute unavailable
Lateststate restored. exec_command pwd returned No usable execution environment is available; selectedenvironment unavailable. **No2024market calculation or archive-byteverification performed.** Advanced reproducible preparation only: exact prior2025code adapted for2024dates/archived SHA/labels, everyothertext unchanged; protocol567bd103 andcode c85ed7eb frozen/readbackexact. ExpectedSHA recovered from prior actual current_native_offline2024report, notnewverifieddownload; officialchecksumURLs inaccessiblethroughwebtool. Need usableenvironment, actualSHAcheck+sourcefixtures, then runfrozen scriptonce andsaveactual results. [Checkpoint](../research/continuation/results/public4_2024_20261021_0138/checkpoint.json). Latest production4bbda6c7SUCCESS/source25388b4; no changes/restart. Computeavailability treatedretryable, no evidence ofpermanentallfutureblock. Existingreferencealerts/taskcontinue; no performanceimprovement orpromotion claimed.


## 2026-10-21 02:35 KST — remote computation resolved local blocker;2024 comparison completed
Localexecstillunavailable. Used single bounded read-only GitHubActions run39467632950/job118783074202 SUCCESS with pinned25388b4source and frozen0138protocol; both2024archivebyteSHA checked beforeactualcalculation,11sourcefixturespass.2024Jan8-15+Jan16followup:101uniquesetups,1watch expired,0opens/cashlegs;zeroDecimalchecks means nofinancialsamples, notprofitabilityproof. FullresultSHA103e9a8843894bee2b37e0a77a91fa7dc759fbcc320ef70aee261702148ec5ae/Gitblob38306f433bee33278b9627dadc2c42dc428c3e65 remoteverified.2025comparison108setups/2negativeboxtrades preserved; no promotion/deployment. Stop furthercalendar sweeps. Next one originalNassigroupeddeclinecontract card with atmost2primary teachingvideos/actualcontenttimestamps, distinguish5m hypothesis vsverified originalrules; executablecorrespondence test onlyif exactrulesavailable. No inventedweights/newtechniques. [Full result and execution](lbank_public4_2024_comparison_2026-10-21.md).


## 2026-10-21 03:30 KST — one original-source contract reconciled
Nassigroupeddecline card completed andreadbackGitblob14aada0222a60c82a726180a83a8ee87e7f11e6e verified. Read actualauthorinterview2021Sept11 pluspinnedexistingcode/doc;2originalteachingvideos attempted, webfailed andvidIQkoINVALID_ARGUMENT, nofreshtranscripts/timestamps obtained. Primarysupportsqualitative5mthree-decline/thirdclose beginnerentry; exactnumericgrouping/initialfixedstop/TP1missing. Distinguish current .5medianTR/1bp/10bar and extra1m/context/RSI/watch assumptions:2024/25results are compositehypothesis, notoriginaltrader performance. No inventedrule/performance test/promotion/deployment. Next oneexistingdoubleBB primarycontractthenpre-frozen pairedtimingtransferwithidenticalrisk/costgates, no newtechnique/calendar sweep;CIavailable. [Full reconciliation](lbank_nassi_source_contract_2026-10-21.md).


## 2026-10-21 04:31 KST — doubleBB timing paired intervention completed
Frozen before outcome; isolated CI [39484591354](https://github.com/hekyun-ctrl/upbit-market-analyzer/actions/runs/39484591354) SUCCESS, original11fixtures. Same six BB development events: original0 vs bypass extra1m/watch0 entries. Context/RSI/risk-target remained blocking. Other strategies identical, box2 losses preserved. No performance claim/promotion/deploy. Full result readback verified blob 011f2211050af290cbed781208c94f38d1b0d64e. See docs/lbank_doublebb_timing_comparison_2026-10-21.md and state next_work: numeric context→RSI→plan rejection attribution for the same6events; no calendar sweep/gate relaxation. Originalvideo newtranscript unavailable due providercredits, no retry; exact originalstop/exit remain missing. Existing referencealerts and riskwarnings maintained.


## 2026-10-21 06:07 KST — same six BB events numerically attributed; native paper open confirmed
Localcompute restored, freshproductionclone25388. Frozen3b2bf688 script actuallyexecuted with original2025ZIP SHA rechecked,11fixtures and329Decimalpredicatechecks.6events/60evaluations, not60trades: context5/RSI30/plan25;25priorgatepasses blocked setupbarrier15/range10. No stopdistance-directfailure, newalpha or policychange. ResultSHA aa0ab194287188114aaa73743a3ca4663770a5257a40a2f0b126fecd6feaf876 readbackblob73095de43375c45ee89240c9a52bbaba03e5a82d. [Actual values](lbank_doublebb_gate_attribution_2026-10-21.md). Separately nativeboxLONG1792530000 watch06:08/open06:10KST, APIopenedack06:10:41; boundedwindow1candidate1open0closed, notprofitability. Productiondeploy4bbda6c7SUCCESS unchanged;no restart. Next linkactualnativeepisodeclosed/censor/notification evidence, then freeze existingRSIstudychronologicalsame-window protocol, preserve source management differences. BBsamecalcfinished;nogate-relaxation/calendar sweep.


## 2026-10-21 07:03 KST — unchanged existing RSI chronological replay executed
Source25388b4, frozen1bc60bb5/code andadfed6c/protocol. Installedmissingccxt4.5.89 then8sourcefixturespass;11520polls/10080ready,37unique(26pullback11divergence),0watch/open/close.0financialDecimalchecks because0legs, notpositiveverification. ActualresultSHA23df7ed066604c4ef439dcb6e36010742657b57dfc28a65bac74c05bcae1bde5 readbackblobc4bd2a21c8409135cfd74362843b8ecef9ddfb2e. Same-window4sourcecomparison2negativeboxtrades retained;0RSItradesnotimprovement. Preserveoriginal RSIobserve(no native_state)/runnerFalse. [Full evidence](lbank_RSI_chronological_replay_2026-10-21.md). Nativebox06:10episode outcome stillunknown inboundedlatestlogs, notproofopen/zeroprofit/nogaps. Nextfinite20actualplanrejections numeric feasiblecostedR attribution onexistingIDs;no calendar sweep/gaterelaxation/newmethods. Userclarification=paperpurpose measurableaccuracyimprovement, notmessagecount; noopschange/promotion/newusernotice.


## 2026-10-21 08:10 KST — actual20 RSI plan-call feasibility finished; one native episode closed
Frozen35334d22, actual20calls/15uniqueIDs; replaywholepriorreportexactmatch.9range/6barrier/5stop>0.6%.19aheadbarrier planningmodelmaxR1.02627,double0.68066,0atleast1.5R;127Decimalchecks are planningpredicates not trades. ResultSHA6ce8f33b03561b2aeb60d79d21a3001f17a11df92e7e39726d91cb8998d12cff/blob b7ba5972ae4d311519c061e4dc8ef856b3d9d756 verified. [Full arithmetic](lbank_RSI_plan_feasibility_2026-10-21.md). Nativebox06:10closed08:10:45KST levels/warning sameepisode time_exit -3.43031893USDTmodel, closedAPIacktrue;runner/TP1fullpath/realizedfunding/deviceunknown. No promotion/livechange/restart, latestremoteprod25388. End unchanged4/RSIsamewindow/gateauditcycle;next originalRRLprimaryfulltext/code/data/netcost/manualexecutionpreflight targetedtoactualfriction/targetgap, no training/headlineadoption/riskrelaxation. Nativeoutcome evidence preserved; no newfailure-only usernotice.


## 2026-10-21 09:11 KST — RRL full primary paper and actual table arithmetic
DownloadedUCLpublished10pages7,398,517bytesSHAe422598ceaa3830bd2f443e1bda32e7c2c57e9409dd8b88c0550ec3da062e944, fulltextread+tables38597/38598renderedverified. Decimalnet3.498/carry.713/execution-.542;carry20.383%ofnet unlikeabstract71%wording, samepathdoubleexecution2.956conditionalnotfullcosttest. Seed250trialCI isnotcorrelatedfutureedge. Exactfeature/inputSHA/code/closedledger/manualstopTP1missing;no newpricebacktest/training/promotion. CC-BYtext/provenance/code/results preserved, reportreadback0464b5fe73cc2f3438ee6cda553cdc98f5a13e4f. [Primarycontract](lbank_RRL_primary_contract_2026-10-21.md). NextdistinctLSOB710eef59fullREADMEonlylead, source_screening0911;maker/spotproxy/risk2%/lowfrequency/192grid/rawCSVgitignored cautions fixed before actualcode/inputpreflight, noauthorheadlineadoption orriskchange. Existingprod25388deploy4bbda6c7SUCCESS unchanged, no restart ornewusernotice.


## 2026-10-21 09:54 KST — original LSOB contract probes executed; raw-ledger next scope
Pinned710eef59fullcode/fetch/tree read, frozenprobe21a472c actualexecution: unchangedprefix/futurecolor changes created4->5; originalexecutionfillbarstopmiss permits+39.886 vssourcefee stop-20.4590945 syntheticfixture. Nooriginalmarket196ledger/rawSHA orfunding, no defaultpricebacktest claimed. Resultreadbackblobc3b6ebf881cf651e93472ea10479bfdb88ab6498. [Fullscope](lbank_LSOB_contract_2026-10-21.md). No repair/grid/promotion/deploy. Bounded3newtreehits havenoCSV/parquet/JSONresults;dailyjhadchitiREADMEexcludedmanualBTCfrequency. Nextreusepreviouslyparkedpublicbuffettlaw45dda879 actualcommittedBTCparquet/history/PnL; headerfirstrowsshowsentry==exithourlylabels, not324closedcampaigns. InspectnotebookJSONtextonly thenBTCcashcontributionifidentifiable; noETH/10xportfolio transfer/optimization/privateexecution. Detailed screening0954 retainsblobsandlimitations. Existingreferencealerts/riskwarningsstay, nofailure-onlyusernotice.


## 2026-10-21 11:20 KST — actual committed BTC component cash attribution
Frozen0792ee9d, original45dda879 notebookJSONtextonly/noexecutionoroptimizer.10sourceSHA/6600continuousrawhours/2184historyhours;BTC recordednet90838.60,ETH302180.54,portfolio393019.14 exactsummaryreconciled. CurrentcloseTheta_t getsalreadyfinishedsamebarreturn;fixedpath1barlagBTCnet-8770.14 diagnosticnotrepairedstrategy.6398BTCcleanprices differfromraw/fullsampleclipping;324portfolio rebalance segments notclosedBTCtrades.6553+4378actualcashchecksnotperformancevalidation. ResultSHAfa3a03e3aa882491a3cc8d5cc6e89cf58807db335088add0bf402008c04955d5/readback51c4026a03b1700d338418d93b44ac1c9f8e416c. [Details](lbank_original_BTC_attribution_2026-10-21.md). No adoption/repair/riskchange/deploy. Nextboundedmax2artifact-firstpublicBTCfixedraw+closedledger/costdiscovery via screening1120;metisdocaggregategridnotshortlisted. Existingreferencealerts/warningsstay;nofailure-onlyusernotice.


### 2026-10-21 12:15 KST — 공개 원장 계산

종료·반전687개와 미종료1개를 구분하여 실제 계산 완료. 자금조달비 제외 체결비 차감 계산41.5874XBT/PF1.346이며 알람 성과로 채택하지 않음. 같은 거래자의 원장/분석 저장소라 독립 표본 아님. 상세 원전·SHA·명령·제약·다음 최초진입 전수 진단: [문서](lbank_public_discretionary_ledger_2026-10-21.md). 운영 변경·배포 없음.
