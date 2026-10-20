# 기존 RSI 별도 연구의 실제 연속사건 검증 — 2026-10-21

## 고정 및 실행
기존 운영25388b431c9c9d38df6a434670586c65f382d604의 lbank_rsi_divergence.DivergenceStudy 원본 그대로. 코드1bc60bb53dba5ccffd397a290a8cf4da21ff08eb와 프로토콜adfed6c850243dab5c90523774ce6e245370d464를 계산 전에 저장했다. [고정 소스](https://github.com/hekyun-ctrl/upbit-market-analyzer/blob/25388b431c9c9d38df6a434670586c65f382d604/lbank_rsi_divergence.py). 유명 트레이더 원전 복제나 새 외부 전략으로 표시하지 않는다.

로컬 실행 가능 확인, dependency ccxt4.5.89 재설치 후 원본 test_lbank_rsi_divergence 8개 통과. 처음 dependency부재로 실행이 중단되었고 설치 완료 후 정상 실행했다. 실패한 호출을 계산 완료로 세지 않았다.

```
python -m unittest test_lbank_rsi_divergence -q
python rsi_replay.py --data data --output rsi_result.json
python verify_rsi.py --data data --report rsi_result.json --output rsi_verification.json
python rsi_diagnostics.py
```

rsi_replay.py는 research/continuation/replay_RSI_20261021_0703.py, verify_rsi.py는 기존 verify_public4_general_20261021_0235.py, rsi_diagnostics.py는 RSI_diagnostics_20261021_0703.py이다. baseline과 같은 로더·원본 AuditBook·비용 계수 사용. adapter는 await_1m_close 결정에 누락된 strategy를 현재 동일분석의 id로 보충하는 출력표시용이고 진입 규칙은 바꾸지 않았다.

자료: 공식 Binance UM BTCUSDT 2025년1월 1분44640연속봉/펀딩93행.
- 봉 https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-01.zip
- 봉 SHA256 b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e
- 펀딩 https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-01.zip
- 펀딩 SHA256 df725010d55458866eb5a47e9ee708c6796c9d7e77b14c38342cea11f381815e

실제 ZIP SHA단언·연속성 검사와 완료봉 미래누출 단언 통과. 같은2025-01-08진입시작~15미만,16미만추적:11520분평가/10080준비평가. 이미 여러 후보에 사용된 개발자료이고 독립OOS가 아니다. 같은 자산의 같은 사건 반복선택 위험을 인정하며 새 달력/문턱을 탐색하지 않았다.

## 실제 결과
서로 다른 후보37: RSI되돌림26, RSI다이버전스11. 반복 결정300회는 거래300건이 아니다.
- 완료1분반응 대기258회
- RSI방향8회
- context14회
- 손절/목표 계획 거절20회

최종 watch0/진입0/종료0. 따라서 비용후 기대값·PF·낙폭·TP1율/하한은 추정 불가(NULL). 코드의 conditional_R=0 합계는 표본 없는 합계이며 무손실/우위가 아니다. Decimal 금융검증0회도 거래다리가 없기 때문이며 검증성공 수익표본으로 세지 않는다.

비용 모형은 편도수수료6bp+미끄러짐2bp, 실제 과거펀딩, 비용2배에서는 지급비용을2배하고 수취액을 부풀리지 않는다. 하지만0진입이므로 이번 창에서 비용후 거래 성과는 관측되지 않았다. 실제 LBank 호가·15초가격·수동체결·긴급경고는 미검증이다. 별도 첫5분 역행/분봉내손절·TP1선행 진단 코드도 실행했으나0진입이라 표본이 없다. 실제 경고 발신률과 혼동하지 않는다.

결과 SHA256 23df7ed066604c4ef439dcb6e36010742657b57dfc28a65bac74c05bcae1bde5, remote blob c4bd2a21c8409135cfd74362843b8ecef9ddfb2e 재조회 일치.
연구 결과/독립검증/진단은 research/continuation/results/RSI_20261021_0703/에 보존.

## 비교의 의미와 제한
같은 창의 기존 공개기법4종은108후보/박스2진입/levels -1.703169R, 비용2배 -2.372083R였고 RSI는37후보/0진입이다. 거래 없는 RSI를 더 우수하다고 판정할 수 없다. 기존 실제 진입22건 관리검증은13검열이 남아 있으므로 전체22기대값은NULL을 유지한다. 집계와 일부 종료 표본만으로 개선됐다고 주장하지 않는다.

DivergenceStudy의 원본 book.observe는 native_state를 받지 않고 add에서 runner_enabled=False다. followup도 동일하게 유지했다. 따라서 TechniqueStudy/native관리의 구조종료를 RSI에 몰래 추가하지 않았고 경고/runner 정책열이 독립 매매라는 뜻도 아니다. 새로운 source가 없는데 임의원칙을 더하거나 느슨한 조건으로 수익 표본을 만들지 않았다.

## native 모의 사건 후속과 다음 유한 단계
기존 boxLONG public_techniques_v1:box_retest_5m:1:1792530000(06:10진입)의06:10~07:04KST 범위 조회 중 최신 study로그는 ready=True지만 종료를 확인하지 못했다. net_usdt_model검색0행은 실제 거래가 계속열림/손익0/손실없음이라는 증거가 아니다. LIMIT로 최신행만 반환되어 전구간 완전성도 미확인이다. 원본 active DB를 현재 로그도구로 직접 조회할 수 없어 종료는미확인으로 유지한다. runtime.json에 원문 저장. 운영 재시작/주문/신호 변경 없음.

다음은 이번37사건 중 실제확인/context/RSI후 calculate_plan에 도달했던20회(서로 다른 ID는 저장결과에서 확인)의 정확한 거절분기·장벽까지 가능한 비용후R·원본 목표 요구R를 기록한다. 기존 BB6수치계산은 반복하지 않는다. 원래 위험/비용/목표게이트 유지 상태에서 source-grounded수정 여지가 있는지 한정하며 수익성 또는 개선 증거를 대체하지 않는다. 사용자 최신 요구대로 모의발신 건수는 정확도개선으로 보고하지 않는다. native1건의 종료/검열·발신실패·공백 후속은 계속 연결한다.
