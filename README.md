# US RS New High Scanner

A no-API-key daily scanner for US-listed common stocks and ADRs using TradingView's public screener feed.

## Signal definition

For each stock and session:

`RS = stock daily close / SPY daily close`

A breakout occurs when a session's RS is strictly greater than the maximum RS over the **preceding 250 completed trading sessions** (the current session is excluded from the comparison). The output includes instruments with at least one such breakout in the latest 10 completed US trading sessions.

The scanner writes:
- `outputs/rs_new_high_tickers.csv` — one column, `symbol`
- `outputs/rs_new_high_details.csv` — symbol, breakout date, RS value and breakout margin

The screener universe excludes funds/ETFs, OTC listings, preferreds, warrants, units, rights and other non-common-equity instrument types. TradingView's screener is an unofficial public endpoint, so coverage is validated and failures stop publication rather than silently writing a partial file.

## Run locally

```bash
python -m pip install -r requirements.txt
python rs_new_high_scan.py
```

## Automation

GitHub Actions runs at 00:40 UTC Monday to Friday and can also be started manually from the Actions tab. The scan only uses completed daily bars. CSV files are committed to `outputs/` after a successful scan.

## Data coverage caveat

TradingView's public screener and the Python `tradingview-screener` package are not a guaranteed institutional security master or market-data SLA. “All US-listed” means all common-stock/ADR instruments discoverable through the configured NASDAQ, NYSE and AMEX screener queries. The run logs the total retrieved universe and fails if query pagination or benchmark/history checks are incomplete. New listings without 250 prior sessions cannot qualify yet.
