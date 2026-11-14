import pandas as pd

from rs_new_high_scan import LOOKBACK, RECENCY, calculate_signals


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
    stock.iloc[20] = float("nan")
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
