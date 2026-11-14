import yfinance as yf

tickers = ["AAPL", "MSFT", "NVDA", "SPY"]
data = yf.download(
    tickers=tickers,
    period="2y",
    interval="1d",
    auto_adjust=False,
    group_by="ticker",
    threads=True,
    progress=False,
    timeout=30,
)
print("SHAPE", data.shape)
print("TICKERS", list(data.columns.get_level_values(0).unique()))
for ticker in tickers:
    if ticker in data.columns.get_level_values(0):
        close = data[ticker]["Close"].dropna()
        print(ticker, len(close), close.index.min(), close.index.max(), close.tail(2).to_dict())

print('Probe completed.')
