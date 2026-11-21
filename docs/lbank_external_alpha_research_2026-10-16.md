# LBank BTCUSDT 선물: 외부자료 기반 신규 수익규칙 탐구 및 적용 보고
**작성일:** 2026-10-16 KST / **상태:** 사용자 요구 재정의 이후 신규 착수한 외부 연구와 코드 검증. 10:23 예약 작업이 24시간 광범위 외부 연구를 실제 완료했다고 소급 주장하지 않는다.

## 목표
사용자 요청의 진짜 의미는 기존 알람 5~6개 사후 분석이 아니라, 전 세계 공개 문헌·검증된 보조지표·선물시장 미시구조·거시자료를 결합해 24시간 수익 기회를 발굴하고 가짜 신호를 걸러 **거래비용 차감 순수익**을 개선하는 새 알람 엔진을 완성하는 것이다. 매일 시드 +10%는 도전 목표이며 예측·보장 결과가 아니다. 실전 사용자 주문을 자동화하지 않는다.

## 검토한 10개 범주의 직접 출처와 실행 가설

| 직접 자료 | 외부에서 검증된 일반 결론 | 새 실험으로 옮길 핵심 | 주의 |
|---|---|---|---|
| Cont, Kukanov, Stoikov, *Price Impact of Order Book Events* (2014), https://arxiv.org/abs/1011.6402 | 최우선 주문흐름 불균형과 단기 가격 반응 연관성 | LBank 자체 호가 잔량 불균형·스프레드·microprice와 이후 5/15/30분 가격 비교 | NYSE 주식 연구. 호가 잔량 한 장면은 체결·취소 주문흐름(OFI)이 아님 |
| *High-frequency dynamics of Bitcoin futures* (2025), https://doi.org/10.1016/j.bir.2025.07.016 | BTC/ETH 무기한선물 거래량/변동성과 시장구조의 연관성 | 유동성 얕음·대량 움직임·가짜 돌파의 연관성 분석 | Binance 자료로 LBank 수익 검증한 것이 아님 |
| Hurst, Ooi, Pedersen, *A Century of Evidence on Trend-Following* (2017), https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing | 여러 글로벌 자산에서 장기 시계열 추세의 경제적 성과 | 장세 구분 뒤 추세추종/박스반전 전략을 서로 다른 모형으로 검증 | 1~15분 BTC 레버리지 성과와 직접 연결 금지 |
| Chi et al., *An empirical investigation on risk factors in cryptocurrency futures* (2023), https://onlinelibrary.wiley.com/doi/10.1002/fut.22425 | 선물 베이시스·모멘텀 팩터 관찰 | 펀딩·베이시스·선물-현물 괴리를 장세 변수로 가설화 | 단일 BTC 5분 방향 정확도 입증 아님 |
| Bysik, Ślepaczuk, *Machine Learning-Based Bitcoin Trading Under Transaction Costs* (2026), https://arxiv.org/abs/2606.00060 | 7만여 시간, 27회 워크포워드. 단순 예측매매는 10bp 비용에서 무너질 수 있으며 비용 경계 실행이 개선된 경우 존재 | 순수익 기대치/비용·실제 호가·슬리피지 간 차이가 작으면 신호 강제 실행을 막는 *별도 A/B 모의정책* | 타거래소·시간대 결과를 LBank에 바로 적용 금지 |
| *Multi-Timeframe Feature Engineering for Bitcoin Market Prediction* (2026), https://www.mdpi.com/2571-9394/8/3/40 | 37개 특성의 AUC 약 0.57~0.61; 총수익도 거래비용으로 소멸 가능 | EMA/ADX/ATR/MACD/더블 볼린저 추가의 진짜 순손익 한계효용 비교 | 지표가 많다는 이유로 수익 가능성 높아지는 것 아님 |
| *Algorithmic crypto trading using information-driven bars, triple barrier labeling and deep learning* (2025), https://link.springer.com/article/10.1186/s40854-025-00866-w | TP/SL/시간장벽 선도달로 현실적인 학습 라벨 생성 | 알람 당시 진입가에서 TP1/STOP 선도달, 이후 기회비용·실행 지연·유효기간 구분 | 동시 충돌·봉 내 미관측 순서를 성공으로 처리 금지 |
| Bailey et al., *Probability of Backtest Overfitting* (2015), https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253 | 여러 규칙 비교에서 과도하게 최적화된 최상위 전략의 실패 확률 | 모든 시도 임계값 기록, CPCV와 embargo 검증 | 높은 과거 승률 자체는 미래 증거가 아님 |
| Bailey, López de Prado, *Deflated Sharpe Ratio* (2014), https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 | 복수 시도·비정규 성과 편향 보정 | 미래 자료에서 순수익·낙폭·기회 누락을 함께 비교 | 전략 최고 성과만 골라 발표 금지 |
| *Two-Tiered Structure of Cryptocurrency Funding Rate Markets* (2026), https://www.mdpi.com/2227-7390/14/2/346 | 거래소별 펀딩/거래비용/시장구조 차이 중요 | LBank 계약 funding 및 타거래소 차이를 진입 확률보다는 레짐 변수로 검증 | 높은 펀딩이 자동 반전 신호는 아님 |

## LBank 원본 데이터 검증

공식 선물 계약 문서 https://www.lbank.com/docs/contract.html 은 다음 공개 REST를 기술한다.
- 선물 호가 depth: https://lbkperp.lbank.com/cfd/openApi/v1/pub/marketOrder (depth, symbol)
- 선물 marketData: 같은 공개 Prefix의 marketData (productGroup), 현재가·mark price·24시간 거래량·펀딩 관련 필드 안내.
- 문서에서 현재 확인된 **원본 선물 OHLCV Kline API 없음**. Spot의 OHLCV를 선물 봉으로 둔갑시키지 않으며, 외부 거래소 선물 봉은 거래소·가격·펀딩 차이를 명확히 태그한다.
- CCXT 라우트 관련 https://github.com/ccxt/ccxt/wiki/lbank 확인.
- 이 대화에서 실제 Railway LIVE에서 공개 LBank **선물 호가 API 수신 성공** 확인: 2026-10-16 11:19 KST 전후 depth_imbalance5 약 -0.478, microprice_offset_bps 약 -0.0033, spread_bps 약 0.012. 이것은 현물 호가, 자동주문, 수익 확정, 매도체결 우세의 증거가 아니라 단일 **호가 잔량** 스냅샷이다.

## 구현 — Q01 신규 LBank 공개 호가 모의 검증

신규 코드 lbank_orderbook_shadow.py, 연결 lbank_futures_monitor.py, 독립 테스트 test_lbank_orderbook_shadow.py.

- 공개 계약 호가를 약 30초마다 별도 daemon thread로 조회. 메인 시장가 체크와 Telegram 위험/진입 경고/수동주문에 영향 없음.
- 상위 5단계 호가잔량 비대칭 I5=(BidQ5-AskQ5)/(BidQ5+AskQ5), top imbalance, microprice, 스프레드 bp, 양쪽 호가 볼륨 기록.
- 탐색용으로 I5의 절댓값 0.25 이상, microprice 같은 방향, 스프레드 4bp 이하의 예비 후보를 표시. **임계치는 예측력 입증 전의 사전 가설**이며 실전 엔트리 게이트에 연결하지 않는다.
- 후속 5/15/30분 실제 관측 호가가 확인되면, 관측 best bid/ask 기준 가상 청산 순수익(bp)에서 시장가 taker 수수료 0.06%씩 왕복을 차감해 표시한다. **실제 슬리피지, 펀딩, 체결/시장가 충격 미확인.** 순수익 시뮬레이션을 사용자의 실현 수익으로 표현하지 않는다.
- 별도 JSONL 기록 디렉터리 /data/lbank_orderbook_shadow, UTC day partition, 후보 아닌 전체 정상 호가도 기록하여 '기회를 놓친 비율' 기준선 확보. 모의 결과는 실제 데이터 누적 후에만 평가.
- 데이터 접속 실패 시 5분에 1회만 경고 로그, 기존 알람에 영향 없음.
- LBank 선물 24시간 depth 스냅샷 자체는 거래소의 매수/매도 신규주문·취소·체결량을 알려주지 않음. 진정한 OFI/CVD를 계산했다고 표현 금지.

## 검증할 7개 새로운 독립 알파 후보

1. 미시구조/호가 imbalance & microprice — 위 실험 구현. 5/15/30분 결과와 시장가 비용·조기반전 비교.
2. **가격구조 돌파 실패** — Donchian 20 돌파 후 실패 vs 돌파선 5분 재지지, ATR/ADX·거래량 검증; 핵심 실패 예: 2026-10-16 07:02 LONG → 07:10 원 신호 조기 무효화 (사용자 07:12 진입과 별개).
3. **압축 후 변동성 팽창** — Bollinger 20/2, 추가 4/4 및 Keltner squeeze, ATR expansion, 거래량 가속. native 원본 OHLCV 확보 이전은 준비만.
4. **추세 지속/눌림 재지지** — EMA9/20/50/200 기울기, ADX/DMI, 5/15/45분 흐름과 이전 고저점 관계, 역추세 재진입 방지. RSI는 보조지표; 07:07·07:09 실제 SHORT 후보가 RSI 방향 veto로 막힌 건의 전후 성과 별도 그림자 추적.
5. **미결제약정/펀딩/베이시스** — funding 공개 데이터 접근·신선도 우선 검증, OI 및 liquidation 자료 없으면 '없음'으로 표시.
6. **이벤트 기반 체계** — CPI/FOMC 발표 전후 변동성 군집·얇아진 스프레드·잘못된 돌파 검토. 뉴스를 방향확정 근거로 사용하지 않음.
7. **거래비용 민감형 다중전략 메타라벨** — 가격·변동성·호가·기존 전략별 독립 신호를 조건부 성공확률과 TP/STOP/time-barrier로 학습. 시작은 단순 로지스틱/GBDT, 데이터 충분할 때만 XGBoost와 시계열 모델 비교.

## 성과 승격 조건과 반증

- 핵심 목표: **진짜 수익기회 재현성(누락 대비), 허위 진입과 조기 무효화 감소, 수수료·슬리피지·펀딩 후 순기대수익, 실전 손절률, 최대낙폭.**
- 1분 호가 표본과 1분 TICKER 표본을 초단위 체결 기록으로 오인하지 않음. 동일 구간 내 TP·SL 동시 통과 시 불확정 또는 보수적으로 손절로 취급.
- 시간 순 분리된 워크포워드 7/30/90일, 중복된 라벨 창 purge 및 embargo, 모든 임계치 후보와 탈락 이유 보존. 기준선: 기존 실전 알람, RSI 단독, 단순 추세추종, 항상 관망.
- 새 모델의 좋은 사례만 선택해 과거 성과 개선 선언 금지. **데이터 부족하면 실전 진입 게이트 유지**하고 연구 데이터 수집만 배포.
- 시드 +10%/일은 도전 지표이지 보장 성과 아님. 위험 확대/고레버리지/매매 빈도 강제 증가로 목표를 맞추려 하지 않음.
- 최근 실전 00:58 LONG 손절 -71.3335 USDT, 07:02 LONG 손절 -60.2912 USDT는 신규 모델 도입 *이전* 사례이며 신규 결과로 소급 귀속 금지. 사용자의 실제 2건 순손실 약 -131.6247 USDT와 미진입 신호는 별도 집계.

## 예약 작업에 대한 엄격한 감사
10월 16일 10:23 최종보고 예약은 실행됐지만, '24회 독립적 새 외부 연구 + 하나의 새 예측공식 수익검증' 완료 증거가 아니었다. 해당 최종보고는 당시 사용자에게 확실히 전달된 것도 아니었다. 현재 연구의 실행 증거는 외부 자료 직접 URL, 호가 API 실제 로그, 신규 코드와 테스트 파일, GitHub 커밋, Railway 성공 배포로 개별 분리한다. 최종 유효성은 미래 손익 실측을 기다려야 한다.
