from datetime import date, timedelta

import pandas as pd

LOOKBACK = 250
RECENCY = 10


def recent_breakouts(rs: pd.Series) -> list[int]:
    signals = []
    for i in range(LOOKBACK, len(rs)):
        if rs.iloc[i] > rs.iloc[i - LOOKBACK:i].max():
            signals.append(i)
    return [i for i in signals if i >= len(rs) - RECENCY]


def test_flat_series_has_no_breakout():
    rs = pd.Series([1.0] * (LOOKBACK + RECENCY))
    assert recent_breakouts(rs) == []


def test_recent_breakout_is_included():
    rs = pd.Series([1.0] * (LOOKBACK + RECENCY))
    rs.iloc[-3] = 1.01
    assert recent_breakouts(rs) == [len(rs) - 3]


def test_breakout_older_than_ten_sessions_is_excluded():
    rs = pd.Series([1.0] * (LOOKBACK + RECENCY + 1))
    rs.iloc[-RECENCY - 1] = 1.01
    assert recent_breakouts(rs) == []


def test_current_bar_is_excluded_from_lookback():
    rs = pd.Series([1.0] * (LOOKBACK + RECENCY))
    rs.iloc[-1] = 1.02
    assert rs.iloc[-1] > rs.iloc[-1 - LOOKBACK:-1].max()
