from tradingview_screener import Query, col

fields = ["name", "exchange", "close", "time",
          "close|1D[0]", "close|1D[1]", "time|1D[0]", "time|1D[1]"]
for ticker, exchange in [("AAPL", "NASDAQ"), ("SPY", "AMEX")]:
    q = (Query().set_markets("america").select(*fields)
         .where(col("name") == ticker, col("exchange") == exchange)
         .limit(1))
    print("QUERY", ticker, q.query, flush=True)
    print("RAW", q.get_scanner_data_raw(), flush=True)

# Explicitly validate pagination range semantics.
for start in (0, 1000, 2000):
    q = (Query().set_markets("america").select("name", "exchange")
         .where(col("type") == "stock", col("exchange") == "NASDAQ")
         .order_by("name").offset(start).limit(start + 1000))
    print("PAGE", start, "RANGE", q.query.get("range"), flush=True)
    count, frame = q.get_scanner_data()
    print("PAGE_RESULT", start, count, len(frame), frame.head(2).to_dict("records"), flush=True)
