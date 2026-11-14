import time
import yfinance as yf
from tradingview_screener import Query, col

q = (Query().set_markets("america").select("name", "exchange", "type", "typespecs")
     .where(col("exchange") == "NASDAQ", col("close") > 0)
     .order_by("name").limit(100))
count, universe = q.get_scanner_data()
symbols = universe["name"].astype(str).tolist()
print("TV_UNIVERSE", count, len(symbols), symbols[:10], flush=True)
start = time.time()
data = yf.download(
    tickers=symbols, period="2y", interval="1d", auto_adjust=False,
    group_by="ticker", threads=8, progress=False, timeout=30,
)
print("ELAPSED", round(time.time()-start, 1), "SHAPE", data.shape, flush=True)
found = []
for ticker in symbols:
    try:
        close = data[ticker]["Close"].dropna()
        if len(close) >= 260:
            found.append((ticker, len(close), str(close.index.min().date()), str(close.index.max().date())))
    except Exception:
        pass
print("COVERAGE", len(found), "/", len(symbols), "SAMPLE", found[:10], flush=True)
