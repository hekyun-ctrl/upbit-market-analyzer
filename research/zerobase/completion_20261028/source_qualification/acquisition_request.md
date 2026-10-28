# 실제 source 차단을 해제하기 위해 필요한 응답

이 문서는 **발송하지 않은 기술 명세 요청 항목**이다. 계정/주문/권한/키 생성이나 유료 구독은 필요하다고 가정하지 않는다. 사용자 또는 거래소 담당자가 공식 공개 기술 문서 링크와 공개 market-data 예제를 전달하면 이어서 검증할 수 있다.

공식 문의 경로: [LBank 고객지원 안내](https://www.lbank.com/uk/support/articles/2085637005588299776)의 Online Customer Service → 기술 문의 → 필요시 ticket. 이번 작업은 문의·티켓·이메일을 보내지 않았다.

## 거래소 기술팀에 확인할 내용

대상은 **LBank BTCUSDT USDT-settled perpetual / SwapU**이다. 현물 `btc_usdt`의 `/v2/kline.do`가 아니다.

1. 공개 futures native 1m/5m candle REST endpoint와 WS 구독 payload, 정식 버전/변경 이력/요청 한도/재연결 방법을 제공해 주세요. 현재 공개 contract 문서는 getTime/instrument/marketData/marketOrder와 WS 주소까지만 확인했습니다.
2. 응답의 instrument/interval/start/end/update/publication timestamp 단위와 시간대, **봉 확정 조건** 및 final/revision 필드를 정의해 주세요. 구간 rollover나 `tradeTime` 변화만으로 final이라고 가정할 수 없습니다. 확정 이후 정정과 중복 메시지 처리 규칙도 필요합니다.
3. candle volume과 turnover의 단위를 명시해 주세요. 예: BTC base quantity, contracts × contract multiplier, 또는 USDT notional. `instrument.volumeMultiple`과 24h `marketData.volume`을 기존 chart candle의 `h`/`i` 의미로 그대로 전용할 근거가 있는지도 확인이 필요합니다.
4. 소비자가 1m를 합친 값과 **독립적으로 발행되는 native 5m**의 정식 조회/구독 경로 및 발행 provenance를 제공해 주세요. 기존 chart feed의 별도 5m 채널이라는 사실만으로 독립성을 증명할 수 없습니다. 같은 BTCUSDT perpetual, 같은 1m/5m 겹침 구간을 비교할 수 있어야 합니다.
5. 완료 rollover와 reconnect/overlap/정정/누락 사례가 포함된 공개 원문 응답 예제를 제공해 주세요. 과거 예제에 원래 collector 수신시각이 없으면 null로 유지하고, 앞으로 수집하는 원문에는 실제 UTC 수신시각과 session/sequence를 별도 기록하겠습니다.
6. 공개 settlement funding history의 timestamp/rate/interval 정의와 조회 방법도 제공해 주세요. 현재 nextFeeTime/current funding rate는 과거 실정산 이력이 아닙니다.

## 원문 수신 후 기존 importer에 넘길 항목

| 근거 | 보존할 정확한 값 | 현재 상태 / 다음 처리 |
|---|---|---|
| source 문서 | 공식 URL, 문서 버전/취득시각, 상품 식별, 필드/최종성/단위 정의 | 해당 futures candle 명세 없음. 문서 수신 후에만 decoder 검토 |
| wire occurrence | 원문 bytes/hex와 SHA256, 실제 `received_at` UTC 원문 정밀도, session, occurrence `seq`, 문서화 `schema` | 기존 실제 6 chart frames만 있음. 모두 final unknown |
| continuity | 실제 restart 이전 session, 관측 gap 시각/사유, `lost_records` 불명은 null, 이전 segment seal | 과거 공백을 0이나 무손실로 만들지 않음 |
| final bar | `period,start,open,high,low,close,volume,turnover,updated` + 문서에 근거한 `final`/`final_basis` | `updated` 등 정규화 의미를 명세에 따라 검토. 현재 decoder는 final=None |
| snapshot bars | 각 `minute_bars`/`five_bars`/사용된 `research_five_bars`의 정확한 최신 occurrence `{seq,index}`, revision, 원문 hash | `FINAL_RECEIPT_UNVERIFIED`를 그대로 유지. nominal close로 known_at 대체 금지 |
| quote poll | 기존 normalized response 원문/정확한 request/receive UTC text, quote receipt ID, 모든 `entry_poll` envelope | 실제 과거 누락 poll 없음. 새로운 자료도 실제 수신분만 연결 |
| derived inputs | EMA/ATR/RSI/momentum/피벗에 사용한 **전체 bar history/순서/receipt refs**, 오른쪽 확인봉의 known_at, quote·sample/cache·policy start state, 코드/config hash | bar hash 또는 gate trace만으로 full derivation 증명이 되지 않음. 비원천 sampled observation과 native candle을 구분 |
| optional context | 해당 판단에서 실제 사용한 context 값과 원문/known_at; 참고 전용 1h/4h도 참고 여부 보존 | 참고값 누락을 새 진입차단으로 만들지 않음 |
| economics | 고정 계획, 당시 공개 instrument/fee source와 receipt, 같은 시점 depth/spread, 실제 funding settlement; 체결 미확인은 명시 | 계정·체결 조회는 이번 scope 밖. 가정 비용/quote 모형을 실제 체결로 표현하지 않음 |

`sealed_received_stream_v1`의 wire/재시작/gap/snapshot 형태와 bounded 원자 importer는 이미 있다. 새 명세가 와도 JSON `verified:true`나 임의 `final:true`로 차단을 해제하지 않는다. 문서에 맞는 **source-dependent decoder/파생 입력 연결의 기존 잔여 부분**을 검토하고, 실제 원문·단위·독립성·연속성 증거가 충족되는지 확인해야 한다. 현재 비교기는 `purpose=unseen`을 항상 차단하므로 자료만 넣고 실제 비교가 실행됐다고 주장할 수 없다.

그 다음 단계는 이미 정한 개발 결과/비용/위험을 고정한 채 미사용 연속 구간을 사전 선언하고, 기존 정책과 선택한 조합의 모든 무신호/탈락/추가·제외·미확정 구간을 비교한 뒤 원래 P4 판단을 하는 것이다. 새 100-paper/30-day 선행조건이나 정책 재조정은 추가하지 않는다.

## TradingView 발견 경로의 정확한 한계

[LBank 공식 안내](https://www.lbank.com/uk/support/articles/48083695479961)는 TradingView의 LBank `BTCUSDT.P` 차트를 안내하고, [TradingView 문서](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/)는 로드된 chart CSV export를 설명한다. 이 경로는 과거 가격 교차확인 후보일 뿐, 위 final/volume/독립 native5m/원래 수신시각을 제공한다는 근거가 없다. CSV 다운로드 시각을 과거 각 봉의 수신시각으로 사용할 수 없다. 이번에는 CSV/비공식 API/프록시 자료를 수집하거나 대체하지 않았다.

현재 자율적으로 가능한 유한 공식 경로 조사는 끝났다. **다음 실행의 트리거는 실제 공식 명세 또는 위 provenance를 갖춘 신규 raw receipt의 도착**이다. 그것 없이 같은 probe/개발 재생/합성시험을 반복해도 필수 P3 비교/P4 판정은 진행되지 않는다.
