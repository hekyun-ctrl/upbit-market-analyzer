# OBV 자료 불일치의 공식 체결 원본 감사 — 2026-10-20

이전 165개 OBV 구성요소 평가에서 제외된 다섯 구간을 공식 체결 원본으로 복구할 수 있는지 실제 검사했다. **체결 원본 15,212,218행을 읽었으나 해제 가능한 격리는 0건이었다.** 2025년을 포함한 전체 달력 성과는 복구하지 못했다. 같은 입력과 기준의 OBV 성과 계산을 반복하지 않고 다음 공개 논문 후보로 이동한다. 기존 부분 구간의 양수를 전체 기간 또는 실전 수익성 근거로 확대하지 않는다.

## 사전 기준과 실제 실행

[판정 규약](../research/continuation/source_reconciliation_protocol_20261020.json)을 [888df860 커밋](https://github.com/hekyun-ctrl/upbit-market-analyzer/commit/888df860a45eed1a7e392e2662c53608f12fbe5b)에 결과 확인 전에 저장했다. 원본 체결 ID가 대상 5분 및 앞뒤 관측에서 연속이고, 실제 체결만으로 만든 다섯 1분봉의 OHLCV가 기존 월별 원본과 절대오차 1e-6 이내로 모두 일치할 때만 격리를 해제한다. 자료를 보간하거나 성과에 유리한 원본을 고르는 경로는 없다. 2022·2023년 격리와 9개월 학습 기준도 유지한다.

[공식 Binance 공개 자료 설명](https://github.com/binance/binance-public-data/)의 USD-M `trades` 형식과 SHA256 `.CHECKSUM`을 사용했다. 네 일별 ZIP의 합계는 125,906,824바이트이며 모두 공시 체크섬과 일치했다. [감사 코드](../research/continuation/reconcile_trade_archives.py)는 CSV를 나누어 읽고 전체 ID·시각 순서, 값과 단위, 대상 구간의 양끝과 ID 연속성을 검사한다. 월별 비교 원본은 기존 Git 보존 manifest의 SHA로 고정했다. 새 환경에서는 필요한 세 달의 1분·5분 ZIP 여섯 개만 복원하며, 기존 로컬 manifest가 남아 있을 필요가 없다. 같은 이름의 월별 파일이 고정 SHA와 다르면 거부한다.

검사 후 공백 진단을 보강하고 입력 복원 경로를 추가한 재실행은 동일 SHA의 자료를 사용했으며, 해제 0건 판정이 같았다. 전략 성과 재계산은 실행하지 않았다.

## 실제 결과

아래 시각은 UTC다. 대상 행 수는 해당 5분 안에서 실제 읽은 원본 체결 수이며, 공백 ID 수는 앞뒤 이웃 체결까지 포함한 검사 문맥의 수치다.

| 대상 5분 시작 | 원본 체결 행 | 빠진 문맥 ID 수 | 미충족 근거 | 판정 |
|---|---:|---:|---|---|
| 2024-10-28 21:10 | 9,136 | 2 | 대상 구간 ID 두 곳 불연속 | 격리 유지 |
| 2025-01-14 13:30 | 129,271 | 10 | 대상 구간 ID 열 곳 불연속 | 격리 유지 |
| 2025-01-14 15:00 | 27,680 | 9 | 대상 구간 ID 아홉 곳 불연속 | 격리 유지 |
| 2025-01-29 01:35 | 8,605 | 2 | 대상 구간 ID 두 곳 불연속 | 격리 유지 |
| 2025-08-29 06:35 | 10,458 | 180 | 앞쪽 이웃 관측 없음; 처음 두 1분 자료 없음; ID 162곳 불연속 | 격리 유지 |

마지막 구간의 첫 관측은 06:37:04.431이다. 다섯 구간 모두 완전성 전제에서 거부됐으므로, 불완전한 체결로 OHLCV를 인증하거나 보충하지 않았다. 이 결과가 기존 월별 1분봉 또는 5분봉 중 하나의 정확성을 입증한 것은 아니다.

ID 불연속만으로 파일 손상 또는 누락 원인을 단정할 수도 없다. [공식 API 설명](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)의 `/fapi/v1/trades`는 호가장에서 체결된 시장 거래를 반환하며 보험기금·ADL 거래를 제외한다. 이런 범위 차이로 ID가 빠질 수 있으나, 이번에 빠진 개별 ID의 원인이 확인된 것은 아니다. **체크섬 일치는 내려받은 바이트의 동일성이지, 모든 거래 종류와 봉 집계의 완전성 보증이 아니다.** 판정 기준은 결과를 보고 완화하지 않았다.

별도로 2026-10-01 공식 1분 일별 ZIP의 SHA와 1,440행·60초 연속성을 확인하고 첫 행을 보존했다. 이는 9월 월말 종료 가격 경계용 후보 자료일 뿐이다. 격리 해제 조건이 충족되지 않아 기존 전략 입력에 추가하거나 10월 수익 계산에 쓰지 않았다.

## 재현 근거

[전체 기계 판독 결과](../research/continuation/results/source_reconciliation_20261020_report.json)에 원본 URL·체크섬 URL·SHA·바이트 수·전체 행 수·분별 실제 행 수·공백 ID 예시·원본 시각과 코드 SHA를 저장했다. 10개 [검사](../research/continuation/test_reconcile_trade_archives.py)가 통과했다. 여기에는 원본 SHA 변조 거부, ID 공백·분 누락·양끝 관측 부재 거부, 같은 시각의 ID 순서, 가격 또는 거래량 불일치 거부가 포함된다. 검사 통과가 전략 수익성 통과를 뜻하지 않는다.

| 일별 원본 | SHA256 |
|---|---|
| BTCUSDT-trades-2024-10-28.zip | `d0f82b3cf3d8d3930c41db7f5aced627e384e9dfbd262e138add65ada9d26309` |
| BTCUSDT-trades-2025-01-14.zip | `52658476c98cf8ef583a6a3f72d8d2a54476364f4007a2c57994231c070ee7d5` |
| BTCUSDT-trades-2025-01-29.zip | `ec489fc590f8afa18e41739e2f4e1bfcf6762855d978a52d5fd3402d92aaf267` |
| BTCUSDT-trades-2025-08-29.zip | `e44e5c96a223a3b957bb7197baf2da67e9b65d0a3b03e23122a070e9cd64ee47` |

프로토콜 SHA는 `b6956b0929dc108cef06be352b1215930db768928a4f88de46341b2b15b10814`, 실행 코드 SHA는 `c736af5f9dc6a7b6d010ca24367dc69668dbd5330074578ae5763368f19692e7`이다.

체크아웃한 연구 저장소에서 실행한다. 경로 인수는 빈 임시 디렉터리도 사용할 수 있다. 월별 파일의 공시 버전이 보존 SHA와 달라졌다면 원본 버전 불일치로 중단하며 다른 버전을 조용히 채택하지 않는다. 원본 URL의 미래 가용성을 보장하는 것은 아니다.

```bash
python -m unittest research.continuation.test_reconcile_trade_archives -v
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python -m research.continuation.reconcile_trade_archives \
  --output /tmp/lbank-source-audit/report.json \
  --raw-folder /tmp/lbank-source-audit/raw \
  --data-folder /tmp/lbank-source-audit/monthly
```

실제로 사용한 출력·원본 경로는 `/workspace/scratch/ba9af914844b/raw-trade-reconciliation-20261020/` 아래였고, 기존 월별 캐시는 `/workspace/scratch/ba9af914844b/binance-futures-verified`였다. 이 경로가 다음 실행에도 남는다고 가정하지 않는다. 원본 ZIP 자체를 Git에 넣는 대신 위 URL·SHA와 복원 코드를 보존한다.

## 단계 전환과 남은 한계

다음은 이미 원문을 확보한 Bouri 외(2021), [On the intraday return curves of Bitcoin](https://repository.essex.ac.uk/30487/)의 Fmean·FPAR 구성요소다. 이전 날까지의 곡선으로 다음 날 진입·청산 시각을 미리 정하는 방법이다. 이번에 방법 부분의 90% 분산 기준과 점수별 상관검정 후 AR(1) 예측을 추가 대조하고, [구성요소 규약](../research/continuation/functional_curve_component_protocol_20261020.json)을 성과 계산 전에 고정했다.

원전 점수 검정의 정확한 지연 차수·추정법과 하루 290개 관측 표기는 재현의 미해결 부분이다. 그래서 182·365개 관측일과 명시한 검정 지연 민감도를 모두 보고하는 공개 방법 기반 이식으로 범위를 제한한다. 원저자 실행 코드는 확인한 기관 자료와 범위가 한정된 검색에서 확보하지 못했으며, 어디에도 없다고 단정하지 않는다. 비용은 원문의 가정으로 낙관하지 않고 기존 보수 비용과 두 배 비용을 적용하도록 고정했다. 아직 해당 모델의 구현·백테스트·수익성 검증은 수행하지 않았다.

후속 작업은 고정 규약에 따른 인과적 구현·검사 후 실제 비용 차감 계산이다. 날짜 공백·미상 손익, 연도별 성과, 모든 사전 고정 변형의 결과와 거래 장부를 보존해야 한다. 기존 자료를 새 독립 구간으로 부르지 않는다. 원전에 없는 고정 TP1·초기 위험 R·긴급 경고·LBank 체결 성능을 임의로 통과 처리하지 않는다. 구성요소에 우위가 발견되더라도 완전한 알람 계약과 기존 전향·실행 기준을 충족하기 전에는 승격할 수 없다.

이번 작업에서 운영 소스·알람 정책을 교체하거나 Railway를 재시작하지 않았다. 합격 후보와 실제 전향 관측은 아직 시작되지 않았다.
