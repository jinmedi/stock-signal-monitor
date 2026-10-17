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

## 웹 대시보드

https://jinmedi.github.io/stock-signal-monitor/

종목별 종가와 단기·중기·장기 스토캐스틱(%K·%D), 교차 지점(▲ 골든 / ▼ 데드), 오늘의 판정표, 최근 신호 이력을 보여줍니다.
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
2. **Actions → Daily signal check → Run workflow** 로 수동 실행해 메시지가 오는지 확인합니다.

같은 봉을 두 번 알리지 않도록 `state/last_signals.json` 에 마지막 처리 날짜를 기록하고, 워크플로가 그 파일을 커밋합니다.

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
  notifier.py     메시지 포맷, 콘솔·Telegram 발송
  state.py        중복 알림 방지 상태 파일
  cli.py          명령행 진입점
  report.py       대시보드용 data.json 생성
site/             정적 대시보드 (index.html, app.js, style.css; Plotly)
```

## 주의

이 도구는 차트 화면을 보고 추정한 지표를 그대로 계산해 알려줄 뿐입니다. 투자 판단과 책임은 사용자에게 있습니다.
