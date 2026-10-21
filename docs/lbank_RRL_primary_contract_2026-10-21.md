# RRL 원문·표 정합성 및 재현 계약 — 2026-10-21

Borrageiro, Firoozye, Barucca, The Recurrent Reinforcement Learning Crypto Agent, IEEE Access10(2022),38590–38599. DOI10.1109/ACCESS.2022.3166599. 게시2022-04-11/currentversion04-15. 저작권 CC-BY4.0이며 저자에게 귀속된다. 원문 https://discovery.ucl.ac.uk/id/eprint/10147117/1/The_Recurrent_Reinforcement_Learning_Crypto_Agent.pdf (UCL publishedversion). 이는 초록만 읽은 이전 조사와 구분된다.

## 실제 확보/검토/실행
웹PDF뷰어가 timeout했으나 공개원문을 curl로 끝까지 내려받아10쪽 전체텍스트를 읽고,38597/38598쪽을 Poppler로 렌더링해 표1·표2를 시각 확인했다. 다운로드 중 불완전PDF에 대한 첫추출은 실패했고 완료후 정상추출했다. 원본7,398,517bytes SHA256 e422598ceaa3830bd2f443e1bda32e7c2c57e9409dd8b88c0550ec3da062e944. CC-BY원문추출은 results/RRL_source_20261021_0911/published_text.txt, 수치계산은 report.json, 코드는 RRL_published_arithmetic_20261021_0911.py에 보존했다. 재현:

```
curl --fail --location --max-time 60 https://discovery.ucl.ac.uk/id/eprint/10147117/1/The_Recurrent_Reinforcement_Learning_Crypto_Agent.pdf --output rrl_published.pdf
pdftotext -layout rrl_published.pdf rrl_published.txt
python reconcile.py
```

reconcile.py는 위코드 복사본이며 원본PDF SHA단언과 Decimal계산을 실제 실행했다. 표의 산술 검산이고 **가격 백테스트·학습·거래원장 재현을 수행한 것이 아니다**. 원문 재현근거가 부족하므로 가상실행으로 빈칸을 만들지 않았다.

## 원문 계약
5분525600관측, BitMEX XBTUSD 호가·거래·펀딩 입력→ESN(100hidden/10feedback/sparsity0.75)→tanh연속포지션[-1,1]. 온라인 quadraticutility, lambda0.00001/베타1/tau0.999. 모든역사를 test로 부르지만 온라인학습과 고정하이퍼파라미터선택이 어느시점에 분리됐는지 재현입력은 없다. 식8은 전기포지션의mid가격변화–현재반스프레드×포지션변화–펀딩×포지션. 식9halfspread와별도5bp거래수수료. 기대순보상mu<0이면닫고 기다린다. 이것은 고정 손절/TP1/수동체결 계약이 아니다.

정확한 외부특징명/전처리/타임스탬프·동일샘플 내 행동순서, 원본호가/거래/펀딩파일SHA, 코드/가중치/시드·종료거래원장 링크를 원문/기관페이지에서 찾지 못했다. PDF URL주석은ORCID4개이며 코드/데이터 링크는 없었다. 제한된공개검색에서도 원저자 코드가 확보되지 않았다. 이것은 존재하지않는다는 단정이 아니다. 데이터/실행의존성을 새로운OHLC로 채우면 다른 전략이 된다. XBTUSD계약을 LBank선형USDT계약으로 바꾸는 정산·사이징·지연·손실한도·TP1정의도 미확인이다. 사용자계좌/원저자연락/새학습은 하지 않았다.

## 실제 표 검산
| 표1(38597쪽), 저자보고 | 값 |
|---|---:|
| 일별행 수(거래수 아님) | 1684 |
| 합계pnl | +3.498 |
| 합계carry | +0.713 |
| 합계execution | -0.542 |
| 평균position | 0.405 |
| IR | 1.46 |
| 최저일pnl | -0.155 |

표와본문은 펀딩이 +71.3퍼센트포인트임을 지지한다. 순합계+349.8%에 대한 비중은20.383%다. 초록/결론의 “수익의71%” 표현과 불일치가 있어 그대로보존했고, 어느문구가저자의의도인지 임의확정하지 않았다. 단순가격성분추론3.327, 펀딩을뺀순합계2.785는 표의 조건부대수이지 새성과가 아니다.

같은포지션경로에서 execution합계만2배하면2.956. 이값을 **전체비용2배후검증수익**으로 쓰지 않는다. 지급·수취펀딩내역이없어 지급만2배하는 스트레스를 계산할수없고, 비용변화시agent의다음행동도달라질수있다.

표2(38598쪽)는 같은시장경로에서250무작위초기화의평균수익2.977, min0.654/max5.021, meanCI2.886~3.069. 이는 새기간이나시계열상관을고려한기대수익95%하한이 아니다. PF/최대낙폭/종료100건/TP1Wilson/긴급경고/수동체결/비용2배/LBank호가증거는 확보하지못했다.

## 판정과 다음
원문은 비용후양수라는 저자보고를 보존하되 **현재공개증거로 우리수동알람에재현·승격하지않는다**. 같은초록/표를 다시계산하거나 새네트워크를학습하며조건을발명하지않는다. 운영 변경/재시작 없음, 기존 참고알람·위험 경고 유지.

다음 별도원전lead는 maxs231/lsob-backtest @710eef5926f9f37f43581b8609c56c8e08f49403. 전체README만읽었으며 코드·원본봉/거래CSV·성과는미검증. 저자196건/2년/PF1.35/+0.269R는 저자주장이다. maker의존·spotproxy·2%위험·평균일1건미달·192grid선택·원본CSVgitignore 한계를 먼저보존했다. 현재후보추가나모의기법추가가 아니다. 정확한backtest_lsob.py/fetch_data_binance.py와고정원본입력/원장을확인하고, 확보가능한경우에만 단한번기본규칙과동일거래의전체비용재가격을재현한다. 192grid나시드탐색/위험확대/실제maker체결가정은하지않는다. 재현불가면확정장애를남기고다른공개원전으로넘긴다. 알람빈도정책을맞추려규칙을느슨하게하지않는다.
