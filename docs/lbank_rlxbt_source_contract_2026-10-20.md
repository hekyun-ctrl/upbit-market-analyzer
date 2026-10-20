# 공개 BTC 복합 전략 실행 규약 검사와 다음 실제 계산 체크포인트

## 복합 전략 검사

원전: [BTC 4-Leg Combo](https://rlxbt.com/articles/btc-4-leg-combo-strategy-sharpe-102-294-return-under-005-commissions), [공개 JSON](https://rlxbt.com/api/articles/btc-4-leg-combo-strategy-sharpe-102-294-return-under-005-commissions). 저자 연구 로그는 `sergio12S/quant-research-log` 커밋 `b4f72a3e65472417ae09e7d7b438523ee6b61e0e`를 고정했다. 이 검사는 원전 실행기의 시장 백테스트가 아니다.

공개 Git 객체 92개의 SHA와 CSV 5개 헤더를 조사했다. 핵심 두 특징량 이름은 검토한 공개 객체에서 발견되지 않았고, 공개 API의 researchManifest도 null이다. 로컬 CSV 경로만으로 원본 자료·종료 거래 원장을 확보했다고 판단하지 않는다. 공개 probe의 `0.01 = 1%` 설명은 비율 단위의 근거지만 해당 기사의 당시 바이너리 버전·실행 순서를 증명하지 않는다.

원문 조건을 바꾸지 않고 합성 특징값·7개 요일의 147개 고정 입력을 실행했다. 롱·숏 동시 조건 5개, 진입·청산 동시 조건 6개, 같은 방향의 복수 조건 5개였다. **시장 발생 빈도가 아니며**, 실행기의 순서가 잘못됐다고 판정한 것도 아니다. 우선순위가 없는 상태에서 연구자가 임의로 순서를 정해 원전 수익률을 재현할 수 없다는 의미다.

가정 비용의 단위 현금흐름도 계산했다. 기준 가격100에서 목표2%·손절1.5%, 편도 수수료0.06%·불리한 체결0.02%·가상 펀딩 준비금0.01%를 사용한다. 이 값은 계정 수수료·실제 LBank 체결이나 전략 손익이 아니다. 기본 롱 목표/손절 순현금은 +1.82839824/-1.66880218, 숏은 +1.83160224/-1.67119818이다. 비용 두 배도 같은 기준 가격으로 비교한다. 수익성·PF·불확실성·TP1·긴급 경고 기준은 미검증이다.

`audit_rlxbt_combo_contract.py`와 8개 검사를 실행했다. 최초 실행은 작업 트리의 데이터 심볼릭 링크를 디렉터리로 읽다가 실패했고, Git 객체를 직접 읽도록 수정했다. 또한 숫자를 Decimal로 옮길 때 문자열을 통하도록 수정하고 float 입력 대조를 추가했다. 개발 중 수정이며 독립 검증이나 사전 고정 성과 평가로 표시하지 않는다. 이 공개 배포본은 핵심 특징량·자료·장부·실행 계약 부재로 교체 근거에서 제외한다. 전략군 전체가 무효라는 결론은 아니다.

## 다음 고정 계산

[1분봉 핀바 원전](https://rlxbt.com/articles/the-1-minute-liquidity-hunt-discovering-the-ultra-sharp-pinbar-rejection-wick-85-range-03-on-21m-btc-bars)은 OHLCV 진입 공식·0.35% 손절·0.70% 목표·25봉 보유 상한을 공개했다. 미정의 지표를 발명할 필요가 없어 다음 실제 가격 계산 대상으로 삼는다. 저자 12개 가설 중 선택 결과와 비용 불명확성을 유지한다. 현물 원전과 선물 전이 검사는 구분한다.

[고정 규약](../research/continuation/pinbar_transfer_protocol_20261020.json)과 [실행 코드](../research/continuation/evaluate_pinbar_transfer.py), 11개 테스트를 과거 손익 계산 전에 Git 체크포인트에 저장한다. 2024년1월~2026년9월 전체를 함께 쓰되 이미 관측한 자료임을 명시한다. 한 해를 골라 일반화하거나 결과를 보고 문턱을 조정하지 않는다. 다음 봉 시가/추가1분 지연, 기본/두 배 전체 비용, 실제 과거 펀딩의 보수적 모형과 공백 미상 처리를 비교한다. 정해진 승격 기준·위험 한도·운영은 변경하지 않는다.

재현 명령은 저장소 루트에서 다음과 같다. 공개 자료는 원본 URL·공시 SHA가 있는 기존 manifest로 복구하며, 임시 캐시의 존속을 전제하지 않는다.

```bash
python -m unittest discover -s research/continuation -p test_rlxbt_combo_contract.py -v
python -m unittest discover -s research/continuation -p test_pinbar_transfer.py -v
python research/continuation/audit_rlxbt_combo_contract.py --source-repo /path/to/pinned/quant-research-log --output-dir /new/combo-results --source-extract research/continuation/results/rlxbt_combo_contract_20261020_final/source_extract.json
python research/continuation/evaluate_pinbar_transfer.py --folder /path/to/verified-Binance-archives --output-dir /new/pinbar-results
```

다음 계산 전에는 운영 재배포를 하지 않는다. 원본 진입 준비가 아직 부족하면 실제 수와 시각만 상태에 기록한다. 이 소스 검사는 사용자 타이밍 민원에 대한 기존 운영 보완의 성과 인증과 별개다.
