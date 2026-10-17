# stock-signal-monitor

스토캐스틱 슬로우 **단기 5-3-3 / 중기 10-6-6 / 장기 20-12-12** 의 골든크로스·데드크로스를
매일 점검해서 매수·매도 신호를 Telegram으로 보내주는 모니터입니다.
서버 없이 GitHub Actions 스케줄로 돌아갑니다.

지표 파라미터의 근거는 [docs/indicator_reverse_engineering.md](docs/indicator_reverse_engineering.md) 에 정리되어 있습니다.

## 신호 규칙

| 상황 | 판정 |
|---|---|
| 단기 %K가 %D를 상향 돌파 | **BUY** |
| 단기 %K가 %D를 하향 돌파 | **SELL** |
| 그 외 | HOLD (알림 없음) |

점수(★ 1~4개)는 다음이 겹칠수록 올라갑니다.

- 과매도권(20 이하)에서 골든크로스 / 과매수권(80 이상)에서 데드크로스
- 중기 10-6-6 이 같은 방향 배열 (%K > %D 이면 상승)
- 장기 20-12-12 이 같은 방향 배열

`config.toml` 의 `require_mid_confirmation = true` 로 바꾸면 중기 배열이 반대일 때는 신호를 보류합니다.
중기·장기 자체의 교차도 참고용으로 함께 알립니다.

## 뉴스·이벤트 필터

교차 신호는 그대로 두고, 그 위에 두 겹의 필터를 얹습니다. 뉴스는 **신호를 만들지 않고** 거르거나 가중치만 줍니다.

| 층 | 동작 | 설정 (`config.toml`) |
|---|---|---|
| 실적 발표 달력 (`events.py`) | 다음 실적 발표 D-3 이내(당일 포함)의 BUY/SELL 은 `실적 발표 D-n, 보류` 를 달고 **HOLD** 로 바꿉니다. 실적일은 yfinance 에서 받아 `state/events.json` 에 7일 캐시하고, 조회에 실패하면 그 종목은 필터를 건너뜁니다. | `[events] earnings_blackout_days = 3` (0 이면 끔) |
| 뉴스 감성 (`news.py`) | 종목별 yfinance 뉴스 + Google News RSS("semiconductor stocks", "반도체 주식")에서 최근 72시간 헤드라인을 모아, Claude(`claude-opus-5-5`) 한 번 호출로 업종·종목별 감성 -2…+2 와 한국어 두 줄 요약을 받습니다. 결과는 `state/news.json` 에 12시간 캐시되어 알림과 대시보드 빌드가 같은 결과를 씁니다. | `[news] weight`, `max_stars`, `hours`, `queries`, `model`, `cache_hours`, `caution_threshold` |

감성 반영 규칙 (`weight = 1` 일 때; 기본값 `0` 은 참고 표시만):

- BUY 는 감성 ≥ 0 이면 ★ +1, ≤ -1 이면 ★ -1. SELL 은 반대(감성 ≤ 0 이면 +1, ≥ +1 이면 -1).
- 별점 최대치는 `max_stars` (기본 5)로 늘어나고, Telegram 메시지 각 종목 아래에 `뉴스: 감성 +1` 과 요약 두 줄이 붙습니다.
- |감성| ≥ `caution_threshold`(기본 2)인 종목은 교차가 없어도 `⚠ 주의` 항목으로 알림에 포함됩니다.

`ANTHROPIC_API_KEY` 가 없거나 호출이 실패하면 헤드라인만 수집하고 감성 없이 동작합니다. `--no-news` 로 수집 자체를 끌 수 있습니다.

### 적중률 추적

매일 `state/track.csv` 에 날짜·종목·교차 종류·감성·종가를 한 줄씩 쌓고, 5·10거래일 뒤 수익률을 채웁니다
(`tracking.py`; 알림 실행과 `report` 실행 모두 갱신하며 같은 날·종목은 한 번만 기록). 대시보드의
"감성별 교차 신호 적중률" 표는 수익률이 확정된 행이 20건 미만이면 "집계 중"으로 표시합니다.
이 표가 쌓인 뒤에 `weight` 를 올릴지 결정하는 것이 의도된 사용법입니다.

### ANTHROPIC_API_KEY 등록과 비용

1. https://console.anthropic.com 에서 API 키를 만듭니다.
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret** 에 `ANTHROPIC_API_KEY` 로 등록합니다.
   `monitor.yml` 과 `pages.yml` 이 이 시크릿을 환경 변수로 넘깁니다. 공개 저장소이므로 키는 시크릿 외의 어디에도 적지 않습니다.
3. 비용: 헤드라인 수십 건을 한 번에 분류하므로 호출 1회에 입력 2~4천, 출력 수백 토큰 수준입니다. 캐시 덕분에 하루 1회(최대 2회)만 호출하며,
   `claude-opus-5-5` 기준(입력 $4 / 출력 $20 per 1M 토큰) 하루 1~3센트, 한 달 1달러 미만입니다. 더 아끼려면 `[news] model` 을 `claude-sonnet-5-5` 나 `claude-haiku-5-5` 로 바꿉니다.

## 웹 대시보드

https://jinmedi.github.io/stock-signal-monitor/

종목별 종가와 단기·중기·장기 스토캐스틱(%K·%D), 교차 지점(▲ 골든 / ▼ 데드), 오늘의 판정표(뉴스 감성 칸 포함), 종목별 뉴스 요약과 헤드라인 링크, 최근 신호 이력, 감성별 적중률 표를 보여줍니다.
달러 가격 옆에는 Yahoo Finance 원/달러(`KRW=X`) 종가로 환산한 원화 금액을 함께 표시합니다. `.KS`/`.KQ` 종목은 원화 그대로 보여줍니다.
`Daily signal check` 가 끝날 때마다, 그리고 `site/` 나 패키지 코드가 바뀔 때마다 `.github/workflows/pages.yml` 이
`python -m stock_signal_monitor.report` 로 `site/data.json` 을 다시 만들어 GitHub Pages에 배포합니다.

로컬에서 보려면 데이터를 만든 뒤 정적 서버로 `site/` 를 띄웁니다.

```bash
python -m stock_signal_monitor.report --out site/data.json
python -m http.server 8765 --directory site
```

## 로컬 실행

```bash
pip install -e .[dev]
python -m stock_signal_monitor --no-state            # 최근 봉 점검, 콘솔 출력
python -m stock_signal_monitor --lookback 20 --all   # 최근 20봉 전체 되짚어 보기
pytest -q
```

Telegram으로도 받으려면 환경 변수를 두고 `--telegram` 을 붙입니다.

```bash
export TELEGRAM_BOT_TOKEN=...   # @BotFather 에서 발급
export TELEGRAM_CHAT_ID=...     # 봇에게 메시지를 보낸 뒤 getUpdates 로 확인
python -m stock_signal_monitor --telegram
```

## GitHub Actions 자동 실행

`.github/workflows/monitor.yml` 이 평일 22:00 UTC(07:00 KST)에 실행됩니다. 미국장 마감 후 일봉이 확정된 뒤입니다.

1. 저장소 **Settings → Secrets and variables → Actions** 에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 를 등록합니다.
   뉴스 감성까지 쓰려면 `ANTHROPIC_API_KEY` 도 등록합니다(위 "ANTHROPIC_API_KEY 등록과 비용" 참고). 없으면 감성 없이 동작합니다.
2. **Actions → Daily signal check → Run workflow** 로 수동 실행해 메시지가 오는지 확인합니다.

같은 봉을 두 번 알리지 않도록 `state/last_signals.json` 에 마지막 처리 날짜를 기록하고, 워크플로가 그 파일과
`state/events.json`(실적일 캐시), `state/news.json`(감성 캐시), `state/track.csv`(적중률 기록)를 함께 커밋합니다.

## 설정

`config.toml` 에서 종목, 파라미터, 과매수·과매도 기준, 알림 옵션을 바꿀 수 있습니다.

```toml
tickers = ["SOXL", "NVDA", "TSLA", "INTC", "TSM", "GLD"]
```

Yahoo Finance 티커를 쓰므로 한국 주식은 `005930.KS` 처럼 적습니다.

## 구조

```
stock_signal_monitor/
  indicators.py   스토캐스틱 슬로우 계산 (SMA 기반)
  signals.py      교차 감지와 BUY/SELL 판정
  data.py         yfinance 로 일봉 조회
  events.py       실적 발표 달력 필터 (D-n 보류), state/events.json 캐시
  news.py         뉴스 수집(yfinance·Google News RSS), Claude 감성 분류, 가중치 적용
  tracking.py     교차 신호 적중률 기록(state/track.csv)과 집계
  notifier.py     메시지 포맷, 콘솔·Telegram 발송
  state.py        중복 알림 방지 상태 파일
  cli.py          명령행 진입점 (필터·가중치 후처리 apply_filters)
  report.py       대시보드용 data.json 생성
site/             정적 대시보드 (index.html, app.js, style.css; Plotly)
```

## 주의

이 도구는 차트 화면을 보고 추정한 지표를 그대로 계산해 알려줄 뿐입니다. 투자 판단과 책임은 사용자에게 있습니다.
