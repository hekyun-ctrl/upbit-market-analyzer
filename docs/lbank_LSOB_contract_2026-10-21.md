# LSOB 고정 원본의 재현 계약 검사 — 2026-10-21

[원본](https://github.com/maxs231/lsob-backtest/tree/710eef5926f9f37f43581b8609c56c8e08f49403) commit710eef5926f9f37f43581b8609c56c8e08f49403. 이전단계는 README만읽었고 이번에 backtest_lsob.py와fetch_data_binance.py 전체 및 tree를읽었다. 원본 source SHA256 a293ced312209e251ac761663b665c845a6f12909795d3df486875ec9cd43507. 테스트코드21a472c7a78fdd0737ebcdced234fd845209b194를 실행전에 저장했다.

## 실제 실행
pandas2.2.3/numpy2.3.5에서 `python check_contract.py` 실행. 원본 backtest_lsob.py를 original.py로두고 research/continuation/LSOB_contract_20261021_0954.py를check_contract.py로복사하면 동일사례를 재현한다. 원본main의 시장다운로드/계좌/주문은실행하지않았다.

1. 동일한첫5봉을고정하고 이후6번째봉의상승/하락만바꿨다. 실제 build_short_setup의created_idx가4→5로변했다. 함수가평가시점뒤의연속하락종료까지읽고, main은created_idx==i인경우에만후보를받기때문에 미래봉이과거후보유무에영향을준다. prefix5봉에서만계산하면4가된다. 이는‘3봉확인’이라는명칭만으로미래누출이없다고판단할수없음을보인다.

2. 발견함수만고정Setup으로주입해 원본run_backtest의fill/exit처리를분리했다. shortlimit100/stop105/target90, 진입봉O99/H106/L99/C101. 시가가진입보다낮고봉고가가손절보다높으므로상승중진입100을지나손절105에도도달하는사례다. 원본은진입봉손절을확인하지않고 다음봉target90을기록해+39.886을냈다. 동일수량4BTC와원본수수료·미끄러짐을적용해진입봉손절을처리한비교계산은-20.4590945. **합성계약검사이지실제시장거래/수익백테스트가아니다.** 패턴발견을주입한범위를명시했고 원본종료함수는수정하지않았다.

## 자료·비용·통계 범위
tree에는원본CSV/거래원장이없다. fetch기본값은현재시각에서730일전까지여서 README의2024Aug~2026Aug196건을고정재현할원본시각/SHA가없고 진행중현재봉도포함가능하다. 현재시각으로받은다른자료를원본196건의재현이라고표현하지않았다. 시장자료SHA/실제거래성과/기대값/PF/낙폭/TP1/긴급경고/수동체결 및비용2배성과는모두미확보다.

추가로 run_backtest는펀딩을반영하지않는다. 진입봉의bodyclose/새extreme을보고restinglimit채결여부를먼저취소하는순서도실제장중순서를해결하지못한다. 끝에서열린포지션은closedledger에없지만equity는청산비용없이mark-to-market한다. total_setups도filled/취소건외cap/liquidation진단수를합산한다. 원본2%위험/10x/196건730일빈도는사용자의운영계약이나일1건기준충족증거가아니다. 192grid/부트스트랩/미래로확인한OOS를새독립검증으로세지않는다.

## 판정
현재공개버전의보고수익은채택근거로쓰지않는다. 코드를고쳐수익이날때까지살리거나같은계약검사를반복하지않는다. LSOB계열전체가무효라는결론도아니다. 원문저자의양수주장은미검증으로보존하고실제LBank성과로바꾸지않았다. 연구결과report.json을remote재조회해blob c3b6ebf881cf651e93472ea10479bfdb88ab6498과전체일치를확인했다. 운영수정·재배포·재시작·신호추가없음.

## 다음 실제 자료 단계
추가짧은선별: jhadchiti/crypto-trading-system README는일간멀티자산trend/spot-perphedge로우리계약과달라제외(계좌/코드실행없음). farrellh1/crypto-funding-rate-strategy, juriok/chart-pattern-backtester, pgup-ai/strategy-lab의tree에저장CSV/parquet/JSON결과가없어원장우선후보로고르지않았다. 전체규칙감사나수익검증은하지않았고존재하지않는다고일반화하지않는다.

새임의기법보다기존에README/tree에서만보류했던 buffettlaw/BTC-ETH-Trend-following-and-Lead-lag-Trading-Strategies@45dda879fbfb3bc6e8dbac46bd8605f01ca28c20의실제커밋원본BTCparquet·포트폴리오history·표를재사용한다. GitHubvisibility public확인. 실제trade_pnl.csv헤더/첫행을읽었더니자산별종료거래가아니라동일시각entry/exit인시간별기록일가능성이있다. 아직이를324종료거래나BTC수익으로세지않았다. 다음유한단계는노트북JSONtext에서BTC/ETH각기여·거래비용·리밸런싱계약을확인해 BTC만의원본현금흐름을분리할수있는지판별하고, 가능한경우고정자료를상대로산술대조를실행하는것이다. 노트북전체/자동실행/.pyc/pickle/계좌접근/원본최적화/10x수익전용은하지않는다. Lead-lag는ETH전용이므로대상제외. 모든양수포트폴리오수익을BTC알람수익으로옮기지않는다. 세부진척·다음원본blob은source_screening_20261021_0954.json에보존했다.
