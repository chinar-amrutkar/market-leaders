import pandas as pd

from rs_new_high_scan import LOOKBACK, RECENCY, calculate_signals, calculate_weekly_lead_signal


def _universe(symbol="TEST"):
    return pd.DataFrame([{
        "name": symbol,
        "description": "Test company",
        "exchange": "NASDAQ",
    }])


def test_recent_rs_breakout_is_detected_on_aligned_dates():
    dates = pd.bdate_range("2025-01-01", periods=LOOKBACK + RECENCY)
    spy = pd.Series(100.0, index=dates)
    stock = pd.Series(100.0, index=dates)
    stock.iloc[-1] = 101.0

    result = calculate_signals(_universe(), {"TEST": stock}, spy)

    assert result["symbol"].tolist() == ["TEST"]
    assert result.iloc[0]["sessions_ago"] == 0
    assert result.iloc[0]["breakout_date"] == dates[-1].strftime("%Y-%m-%d")
    assert abs(result.iloc[0]["breakout_pct"] - 1.0) < 1e-9


def test_breakout_older_than_ten_sessions_is_excluded():
    dates = pd.bdate_range("2025-01-01", periods=LOOKBACK + RECENCY + 1)
    spy = pd.Series(100.0, index=dates)
    stock = pd.Series(100.0, index=dates)
    stock.iloc[-RECENCY - 1] = 101.0

    result = calculate_signals(_universe(), {"TEST": stock}, spy)

    assert result.empty


def test_missing_stock_session_is_not_positionally_paired_to_spy():
    dates = pd.bdate_range("2025-01-01", periods=LOOKBACK + RECENCY + 1)
    spy = pd.Series(100.0, index=dates)
    stock = pd.Series(100.0, index=dates)
    stock.iloc[-20] = float("nan")
    stock.iloc[-1] = 110.0

    result = calculate_signals(_universe(), {"TEST": stock}, spy)

    # The missing bar remains a gap on the SPY session index. Since the
    # preceding 250-session window is incomplete, no breakout is fabricated.
    assert result.empty


def test_flat_relative_strength_has_no_breakout():
    dates = pd.bdate_range("2025-01-01", periods=LOOKBACK + RECENCY)
    spy = pd.Series(100.0, index=dates)
    stock = pd.Series(100.0, index=dates)

    result = calculate_signals(_universe(), {"TEST": stock}, spy)

    assert result.empty

from rs_new_high_scan import _universe_query


def test_common_universe_query_keeps_exchange_type_and_common_filters():
    query = _universe_query("NASDAQ", "stock", 1000)
    filters = query.query["filter"]
    assert query.query["range"] == [1000, 2000]
    assert {item["left"] for item in filters} == {"exchange", "close", "type", "typespecs"}
    assert next(item for item in filters if item["left"] == "exchange")["right"] == "NASDAQ"
    assert next(item for item in filters if item["left"] == "type")["right"] == "stock"


def test_depositary_receipt_query_is_exchange_scoped():
    query = _universe_query("NYSE", "dr", 0)
    filters = query.query["filter"]
    assert query.query["range"] == [0, 1000]
    assert {item["left"] for item in filters} == {"exchange", "close", "type"}
    assert next(item for item in filters if item["left"] == "exchange")["right"] == "NYSE"
    assert next(item for item in filters if item["left"] == "type")["right"] == "dr"


from rs_new_high_scan import (
    MIN_ADR_PCT,
    MIN_AVG_DOLLAR_VOLUME_30D,
    MIN_AVG_VOLUME_10D,
    MIN_PRICE,
    apply_liquidity_filters,
)


def _liquidity_rows():
    return pd.DataFrame([
        {"name": "PASS", "industry": "Software", "close": 25, "SMA30": 24,
         "ADRP": 5, "average_volume_10d_calc": 500_000,
         "average_volume_30d_calc": 2_000_000},
        {"name": "LOW_PRICE", "industry": "Software", "close": 9.99, "SMA30": 24,
         "ADRP": 5, "average_volume_10d_calc": 500_000,
         "average_volume_30d_calc": 2_000_000},
        {"name": "LOW_DOLLAR_VOL", "industry": "Software", "close": 25, "SMA30": 24,
         "ADRP": 5, "average_volume_10d_calc": 500_000,
         "average_volume_30d_calc": 1_000_000},
        {"name": "LOW_SHARE_VOL", "industry": "Software", "close": 25, "SMA30": 24,
         "ADRP": 5, "average_volume_10d_calc": 350_000,
         "average_volume_30d_calc": 2_000_000},
        {"name": "LOW_ADR", "industry": "Software", "close": 25, "SMA30": 24,
         "ADRP": 4, "average_volume_10d_calc": 500_000,
         "average_volume_30d_calc": 2_000_000},
        {"name": "BIOTECH", "industry": "Biotechnology", "close": 25, "SMA30": 24,
         "ADRP": 5, "average_volume_10d_calc": 500_000,
         "average_volume_30d_calc": 2_000_000},
    ])


def test_price_and_nel_liquidity_filters_include_biotech():
    result = apply_liquidity_filters(_liquidity_rows())
    assert result["name"].tolist() == ["PASS", "BIOTECH"]
    assert MIN_PRICE == 10
    assert MIN_AVG_DOLLAR_VOLUME_30D == 30_000_000
    assert MIN_AVG_VOLUME_10D == 350_000
    assert MIN_ADR_PCT == 4


def test_liquidity_filters_reject_missing_or_invalid_values():
    rows = _liquidity_rows()
    rows.loc[0, "ADRP"] = None
    result = apply_liquidity_filters(rows)
    assert "PASS" not in result["name"].tolist()



def _weekly_test_series(weeks=280):
    dates = pd.bdate_range("2019-01-07", periods=weeks * 5)
    spy = pd.Series(100.0, index=dates)
    stock = pd.Series(100.0, index=dates)
    return dates, stock, spy


def test_weekly_rs_lead_persists_until_price_makes_250_week_high():
    dates, stock, spy = _weekly_test_series(280)
    # Create a prior 250-week price high, then pull back and hold below it.
    stock.iloc[100 * 5:105 * 5] = 120.0
    stock.iloc[105 * 5:] = 110.0
    # RS breaks to a 250-week high in week 260 while price stays below its high.
    spy.iloc[260 * 5:261 * 5] = 80.0

    signal = calculate_weekly_lead_signal(stock, spy)
    assert signal is not None
    assert signal["date"] == dates[260 * 5 + 4].strftime("%Y-%m-%d")
    assert signal["price_vs_high_pct"] < 0

    # The signal is still active 20 weeks after the RS breakout.
    signal_week_20 = calculate_weekly_lead_signal(
        stock.iloc[:280 * 5], spy.iloc[:280 * 5]
    )
    assert signal_week_20 is not None
    assert signal_week_20["date"] == signal["date"]


def test_weekly_rs_lead_disappears_when_price_breaks_250_week_high():
    dates, stock, spy = _weekly_test_series(282)
    stock.iloc[100 * 5:105 * 5] = 120.0
    stock.iloc[105 * 5:] = 110.0
    spy.iloc[260 * 5:261 * 5] = 80.0
    # Week 21 after the RS breakout: price makes a new 250-week high.
    stock.iloc[281 * 5:282 * 5] = 130.0

    assert calculate_weekly_lead_signal(stock, spy) is None


def test_weekly_signal_ignores_incomplete_current_week():
    dates, stock, spy = _weekly_test_series(280)
    stock.iloc[260 * 5:261 * 5] = 110.0
    spy.iloc[260 * 5:261 * 5] = 80.0
    # Remove Friday: the current week is incomplete and must not create a signal.
    stock = stock.iloc[:-1]
    spy = spy.iloc[:-1]

    assert calculate_weekly_lead_signal(stock, spy) is None
