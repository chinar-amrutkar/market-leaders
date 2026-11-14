# US RS New High Scanner

A no-API-key daily scanner for US-listed common stocks and depositary receipts (ADRs).

## Signal definition

For each stock and US trading session:

`RS = stock daily close / SPY daily close`

A breakout occurs when a session's RS is strictly greater than the maximum RS over the **preceding 250 completed trading sessions** (the current session is excluded from the comparison). The output includes instruments with at least one such breakout in the latest 10 completed US trading sessions.

## Data sources and universe

- **Universe discovery:** TradingView's public US stock screener, via `tradingview-screener`. The scanner requests NASDAQ, NYSE and NYSE American (AMEX) common shares plus depositary receipts.
- **Daily prices:** Yahoo Finance's public daily-history feed, via `yfinance`. No API key is required.
- **Benchmark:** SPY daily closes from the same Yahoo Finance history source.

TradingView's screener endpoint does not return historical values for the `close|1D[n]` / `time|1D[n]` fields used in the first implementation. The scanner therefore uses TradingView only for the instrument universe and retrieves dated daily bars from Yahoo Finance. Every stock series is reindexed to SPY's exact US trading-session dates before calculating RS. Missing stock sessions remain missing; they are never paired positionally with another date.

The universe excludes ETFs/funds, OTC listings, preferreds, warrants, units, rights and other non-common-equity instruments. The scanner validates the returned exchange and instrument types and fails rather than publishing an incorrectly filtered universe.

## Outputs

- `outputs/rs_new_high_tickers.csv` — one column, `symbol`
- `outputs/rs_new_high_details.csv` — symbol, company, exchange, breakout date, sessions ago, RS ratio, prior 250-session RS high and breakout margin

## Run locally

```bash
python -m pip install -r requirements.txt
python rs_new_high_scan.py
```

Run the regression suite with:

```bash
python -m pip install pytest
python -m pytest -q
```

## Automation

GitHub Actions runs at 00:40 UTC Monday to Friday and can also be started manually from the Actions tab. It runs regression tests, scans the latest completed daily bars, commits the CSV outputs, and deploys the updated dashboard to GitHub Pages.

## Data coverage caveat

TradingView and Yahoo Finance are public, unofficial data interfaces and do not provide an institutional market-data SLA. Availability, symbol mapping and historical coverage can change or be rate-limited. Yahoo Finance history coverage must be at least 70% of the discovered universe or the scan refuses to publish. Newly listed shares without 260 daily observations cannot qualify. “All US-listed” means the common-stock/ADR instruments discoverable through the configured NASDAQ, NYSE and AMEX screener queries and available through Yahoo Finance.

## Dashboard

The static dashboard is published through GitHub Pages:

`https://chinar-amrutkar.github.io/non-extended-leaders/`

The dashboard reads `outputs/rs_new_high_details.csv` and provides search, sortable columns, copy-tickers and CSV download. To enable Pages, go to **Settings → Pages → Build and deployment** and select **GitHub Actions** as the source.
