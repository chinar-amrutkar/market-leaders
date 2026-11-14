"""Small deterministic regression test for the rolling RS-high definition."""
import pandas as pd

LOOKBACK = 250
RECENCY = 10


def find_recent_highs(rs: pd.Series) -> list[int]:
    hits = []
    for i in range(LOOKBACK, len(rs)):
        if rs.iloc[i] > rs.iloc[i - LOOKBACK:i].max():
            hits.append(i)
    return [i for i in hits if i >= len(rs) - RECENCY]


def main():
    # Flat RS series has no breakout. A single higher value in the last 10 bars does.
    flat = pd.Series([1.0] * (LOOKBACK + RECENCY))
    assert find_recent_highs(flat) == []
    breakout = flat.copy()
    breakout.iloc[-3] = 1.01
    assert find_recent_highs(breakout) == [len(breakout) - 3]
    # The comparison excludes the current observation.
    assert breakout.iloc[-3] > breakout.iloc[-3 - LOOKBACK:-3].max()
    print("RS rolling-high checks passed.")


if __name__ == "__main__":
    main()
