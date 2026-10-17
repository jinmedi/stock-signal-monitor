# stock-signal-monitor

스토캐스틱 슬로우 **단기 5-3-3 / 중기 10-6-6 / 장기 20-12-12** 의 골든크로스·데드크로스를 매일 점검해
매수·매도 신호를 Telegram으로 보내고, 같은 결과를 웹 대시보드로 보여주는 모니터입니다.
서버 없이 GitHub Actions 스케줄로 돌아가며, 실적 발표 일정과 뉴스 감성을 필터로 얹을 수 있습니다.

- 대시보드: https://jinmedi.github.io/stock-signal-monitor/
- 지표 파라미터의 근거: [docs/indicator_reverse_engineering.md](docs/indicator_reverse_engineering.md)
- 교차 매매 백테스트 기록(SOXL, 정방향·역방향): [docs/backtest_soxl_2026-04-14.md](docs/backtest_soxl_2026-04-14.md)

## 동작 개요

```
매일 22:00 UTC (07:00 KST, 평일)
  Daily signal check ─ yfinance 일봉 → 스토캐스틱 교차 판정 → 실적일·뉴스 필터 → Telegram 발송 → state/ 커밋
          └─ 끝나면 Deploy dashboard ─ site/data.json 생성 → GitHub Pages 배포
```

## 신호 규칙

| 상황 | 판정 |
|---|---|
| 단기 %K가 %D를 상향 돌파 (골든크로스) | **BUY** |
| 단기 %K가 %D를 하향 돌파 (데드크로스) | **SELL** |
| 그 외 | HOLD (알림 없음) |

점수(★)는 다음이 겹칠수록 올라갑니다. 기본 최대 5개이며 뉴스 가중치를 끄면 4개까지만 채워집니다.

- 과매도권(20 이하)에서 골든크로스 / 과매수권(80 이상)에서 데드크로스
- 중기 10-6-6 이 같은 방향 배열 (%K > %D 이면 상승)
- 장기 20-12-12 이 같은 방향 배열
- 뉴스 감성이 같은 방향 (`[news] weight = 1` 일 때만)

`[alerts] require_mid_confirmation = true` 로 바꾸면 중기 배열이 반대일 때는 신호를 보류합니다.
중기·장기 자체의 교차도 참고용으로 함께 알립니다.

## 뉴스·이벤트 필터

교차 신호는 그대로 두고 그 위에 두 겹의 필터를 얹습니다. 뉴스는 **신호를 만들지 않고** 거르거나 가중치만 줍니다.

| 층 | 동작 | 설정 (`config.toml`) |
|---|---|---|
| 실적 발표 달력 (`events.py`) | 다음 실적 발표 D-3 이내(당일 포함)의 BUY/SELL 은 `실적 발표 D-n, 보류` 를 달고 **HOLD** 로 바꿉니다. 실적일은 yfinance 에서 받아 `state/events.json` 에 7일 캐시합니다. ETF(SOXL, GLD, VOO, JEPQ 등)처럼 실적일이 없는 종목은 필터를 건너뜁니다. | `[events] earnings_blackout_days = 3` (0 이면 끔) |
| 뉴스 감성 (`news.py`) | 종목별 헤드라인(yfinance 종목 뉴스, 비어 있으면 Google News RSS `"<티커> stock"` 검색)과 업종 검색어(`"semiconductor stocks"`, `"반도체 주식"`)로 최근 72시간 헤드라인을 모아, Claude(`claude-opus-5-5`) 한 번 호출로 업종·종목별 감성 -2…+2 와 한국어 두 줄 요약을 받습니다. 결과는 `state/news.json` 에 6시간 캐시되어 알림과 대시보드 빌드가 같은 값을 쓰고, 종목 목록이 바뀌면 즉시 다시 분류합니다. | `[news] enabled`, `weight`, `max_stars`, `hours`, `queries`, `model`, `cache_hours`, `caution_threshold` |

감성 반영 규칙 (`weight = 1` 일 때; 기본값 `0` 은 참고 표시만):

- BUY 는 감성 ≥ 0 이면 ★ +1, ≤ -1 이면 ★ -1. SELL 은 반대(감성 ≤ 0 이면 +1, ≥ +1 이면 -1).
- Telegram 메시지 각 종목 아래에 `뉴스: 감성 +1` 과 요약 두 줄이 붙고, 맨 아래에 업종 뉴스 요약과 적용 환율이 붙습니다.
- |감성| ≥ `caution_threshold`(기본 2)인 종목은 교차가 없어도 `⚠ 주의` 항목으로 알림에 포함됩니다.

`ANTHROPIC_API_KEY` 가 없거나 호출이 실패하면 헤드라인만 수집하고 감성 없이 동작합니다. `--no-news` 로 수집 자체를 끌 수 있습니다.

### 적중률 추적

매일 `state/track.csv` 에 날짜·종목·교차 종류·감성·종가를 한 줄씩 쌓고, 5·10거래일 뒤 수익률을 채웁니다
(`tracking.py`; 알림 실행과 대시보드 빌드 모두 갱신하며 같은 날·종목은 한 번만 기록). 대시보드의
"감성별 교차 신호 적중률" 표는 수익률이 확정된 행이 20건 미만이면 "집계 중"으로 표시합니다.
이 표가 쌓인 뒤에 `weight` 를 올릴지 결정하는 것이 의도된 사용법입니다.

## 웹 대시보드

https://jinmedi.github.io/stock-signal-monitor/

- 오늘의 신호 표: 종목별 판정과 별점, 종가(달러·원화), 뉴스 감성, 단기·중기·장기 K/D 와 배열 방향. 행을 누르면 그 종목 차트로 바뀝니다.
- 4단 차트: 종가, 단기·중기·장기 스토캐스틱(%K 파랑, %D 주황), 교차 지점(▲ 골든 / ▼ 데드), 과매도·과매수 음영. 60봉/전체 전환, 라이트·다크 모드 자동.
- 종목별 뉴스 요약과 헤드라인 링크, 업종 뉴스 요약.
- 최근 60봉의 매수·매도 신호 이력과 근거.
- 감성별 교차 신호 적중률 표.

원화는 Yahoo Finance 원/달러(`KRW=X`) 종가로 환산합니다. 대시보드는 각 봉 날짜의 환율, Telegram 메시지는 발송 시점의 최신 환율을 쓰므로 두 곳의 원화가 조금 다를 수 있습니다. `.KS`/`.KQ` 종목은 원화 그대로 표시합니다.

## 처음 설정하기

이 저장소를 포크하거나 복제해 쓸 때 필요한 단계입니다.

1. **Telegram 봇**: `@BotFather` 에서 `/newbot` 으로 토큰을 받습니다. 봇에게 아무 메시지나 보낸 뒤
   `https://api.telegram.org/bot<토큰>/getUpdates` 의 `chat.id` 를 확인합니다. 봇은 먼저 말을 건 사람에게만 메시지를 보낼 수 있습니다.
2. **Secrets 등록**: 저장소 **Settings → Secrets and variables → Actions → Repository secrets** 에
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 를 넣습니다. 뉴스 감성까지 쓰려면 `ANTHROPIC_API_KEY`(https://console.anthropic.com) 도 넣습니다.
   Variables 탭이나 Dependabot·Codespaces 시크릿이 아니라 Actions 의 Repository secrets 여야 합니다.
   ```bash
   gh secret set TELEGRAM_BOT_TOKEN --repo <owner>/<repo>
   ```
3. **GitHub Pages**: **Settings → Pages → Build and deployment → Source** 를 **GitHub Actions** 로 둡니다.
   ```bash
   gh api -X POST repos/<owner>/<repo>/pages -f build_type=workflow
   ```
4. **첫 실행**: **Actions → Daily signal check → Run workflow** 로 수동 실행해 Telegram 메시지가 오는지 확인합니다.
   끝나면 Deploy dashboard 가 이어서 돌아 Pages 주소에 대시보드가 뜹니다.

비용: 뉴스 분류는 헤드라인 수십 건을 한 번에 보내므로 호출 1회에 입력 수천, 출력 수백 토큰 수준이고 캐시 덕에 하루 1~2회만 호출합니다.
`claude-opus-5-5` 기준(입력 $4 / 출력 $20 per 1M 토큰) 한 달 1달러 미만입니다. `[news] model` 을 `claude-sonnet-5-5` 나 `claude-haiku-5-5` 로 바꾸면 더 줄어듭니다.

## GitHub Actions

| 워크플로 | 트리거 | 하는 일 |
|---|---|---|
| `Daily signal check` (`monitor.yml`) | 평일 22:00 UTC, 수동 | 신호 판정·필터·Telegram 발송. `state/` 아래 상태 파일을 `[skip ci]` 커밋으로 저장 |
| `Deploy dashboard` (`pages.yml`) | Daily signal check 완료 후, `site/`·패키지 코드·`config.toml` 푸시, 수동 | `site/data.json` 생성 후 GitHub Pages 배포 |
| `Tests` (`test.yml`) | main 푸시(`state/` 제외), PR | `pytest` |

`state/` 에는 `last_signals.json`(같은 봉 중복 알림 방지), `events.json`(실적일 캐시), `news.json`(감성 캐시), `track.csv`(적중률 기록)가 들어 있고 워크플로가 매일 갱신해 커밋합니다.

## 로컬 실행

```bash
pip install -e .[dev]
pytest -q

python -m stock_signal_monitor --no-state --no-news       # 최근 봉 점검, 콘솔 출력 (뉴스 없이)
python -m stock_signal_monitor --lookback 20 --all        # 최근 20봉 되짚어 보기
python -m stock_signal_monitor --no-state                 # 뉴스 수집 포함 (ANTHROPIC_API_KEY 가 있으면 감성 분류)
```

Telegram으로도 받으려면 환경 변수를 두고 `--telegram` 을 붙입니다.

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export ANTHROPIC_API_KEY=...   # 선택
python -m stock_signal_monitor --telegram
```

대시보드를 로컬에서 보려면 데이터를 만든 뒤 정적 서버로 `site/` 를 띄웁니다.

```bash
python -m stock_signal_monitor.report --out site/data.json --no-news
python -m http.server 8765 --directory site
```

주요 옵션: `--config`, `--state`/`--no-state`, `--events-cache`, `--news-cache`, `--no-news`, `--track`, `--lookback N`, `--all`, `--telegram`, `--quiet-when-empty`.
`report` 는 `--out`, `--state-dir`, `--no-news`, `--bars`, `--signal-lookback` 를 받습니다.

## 설정

`config.toml` 에서 종목, 파라미터, 과매수·과매도 기준, 알림·이벤트·뉴스 옵션을 바꿉니다. 푸시하면 다음 실행부터 반영되고 대시보드도 다시 배포됩니다.

```toml
tickers = [
  "SOXL", "BMNR", "VOO", "NVDA", "TSLA", "GLD", "AMZN", "AAPL",
  "JEPQ", "PLTR", "IREN", "SPCX", "GOOG", "FRMI",   # 보유 종목
  "INTC", "TSM",                                     # 반도체 참고 종목
]
```

Yahoo Finance 티커를 쓰므로 한국 주식은 `005930.KS` 처럼 적습니다. 종목을 바꾸면 뉴스 감성 캐시가 무효화되어 다음 실행에서 새로 분류합니다.

## 구조

```
stock_signal_monitor/
  indicators.py   스토캐스틱 슬로우 계산 (SMA 기반)
  signals.py      교차 감지와 BUY/SELL 판정 (순수 함수)
  data.py         yfinance 로 일봉 조회
  fx.py           원/달러 환율 조회와 환산
  events.py       실적 발표 달력 필터 (D-n 보류), state/events.json 캐시
  news.py         뉴스 수집(yfinance·Google News RSS), Claude 감성 분류, 가중치 적용
  tracking.py     교차 신호 적중률 기록(state/track.csv)과 집계
  notifier.py     메시지 포맷, 콘솔·Telegram 발송
  state.py        중복 알림 방지 상태 파일
  cli.py          명령행 진입점 (필터·가중치 후처리 apply_filters)
  report.py       대시보드용 data.json 생성
site/             정적 대시보드 (index.html, app.js, style.css; Plotly)
state/            워크플로가 매일 갱신하는 상태 파일
docs/             지표 역추적 기록, 백테스트 기록
tests/            pytest (네트워크·API 호출은 모두 모킹)
```

## 알려진 한계

- 교차 신호는 방향이 바뀐 뒤에 알려주는 후행 지표입니다. SOXL 6개월 백테스트에서는 어느 방향으로 매매해도 그냥 보유한 것보다 뚜렷이 낫지 않았습니다([docs/backtest_soxl_2026-04-14.md](docs/backtest_soxl_2026-04-14.md)).
- 뉴스 감성은 헤드라인만 보고 모델이 매긴 추정치이며, 종목 검색이 겹쳐 다른 종목의 뉴스가 섞일 수 있습니다. 가중치는 적중률 표를 보고 올리세요.
- 일봉 확정 후 하루 한 번만 점검하므로 장중 급변은 다음 날 아침에 알게 됩니다.
- Yahoo Finance 데이터는 HTS 종가와 소수점 단위로 다를 수 있습니다.

## 주의

이 도구는 차트 화면을 보고 추정한 지표를 그대로 계산해 알려줄 뿐입니다. 투자 판단과 책임은 사용자에게 있습니다.
