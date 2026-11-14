from tradingview_screener import Query, col

for exchange in ("NASDAQ", "NYSE", "AMEX"):
    for kind in ("stock", "dr"):
        q = (Query().set_markets("america")
             .select("name", "exchange", "type", "typespecs", "description")
             .where(col("exchange") == exchange, col("close") > 0,
                    col("type") == kind)
             .order_by("name").limit(20))
        count, frame = q.get_scanner_data()
        print("UNIVERSE", exchange, kind, "count", count,
              "rows", len(frame), "sample", frame.head(10).to_dict("records"), flush=True)

# Confirm that historical OHLC/time offsets are not returned by the screener.
q = (Query().set_markets("america")
     .select("name", "close", "time", "close|1D[0]", "close|1D[1]", "time|1D[0]")
     .where(col("name") == "AAPL", col("exchange") == "NASDAQ").limit(1))
print("HISTORY_FIELDS", q.get_scanner_data_raw(), flush=True)

print('ADR probe rerun')

print('Second probe trigger')
