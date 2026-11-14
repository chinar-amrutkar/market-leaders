#!/usr/bin/env python3
"""Scan US common stocks/ADRs for recent 250-session RS highs vs SPY.

RS_t = stock close_t / SPY close_t.
A signal on date t requires RS_t > max(RS[t-250:t]), excluding t.
The output includes symbols with >=1 signal in the latest 10 completed sessions.
"""
from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from tradingview_screener import Query, col

LOG = logging.getLogger("rs_scan")
EXCHANGES = ("NASDAQ", "NYSE", "AMEX")
LOOKBACK = 250
RECENCY = 10
MIN_HISTORY = LOOKBACK + RECENCY + 5
PAGE_SIZE = 1000
MAX_PAGES = 20
OUT = Path("outputs")


def get_screener_universe() -> pd.DataFrame:
    """Retrieve all available common-stock instruments using offset pagination."""
    frames: list[pd.DataFrame] = []
    seen: set[str] = set()
    for exchange in EXCHANGES:
        offset = 0
        for page in range(MAX_PAGES):
            query = (
                Query()
                .set_markets("america")
                .select("name", "description", "exchange", "type", "subtype", "close")
                .where(
                    col("type") == "stock",
                    col("exchange") == exchange,
                    col("close") > 0,
                )
                .order_by("name")
                .offset(offset)
                .limit(PAGE_SIZE)
            )
            count, frame = query.get_scanner_data()
            if frame is None or frame.empty:
                break
            frame = frame.reset_index(drop=True)
            symbol_col = "name" if "name" in frame.columns else frame.columns[0]
            symbols = frame[symbol_col].astype(str).str.upper().str.strip()
            fresh = ~symbols.isin(seen)
            frame = frame.loc[fresh].copy()
            symbols = symbols.loc[fresh]
            if frame.empty:
                break
            seen.update(symbols.tolist())
            frames.append(frame)
            LOG.info("%s page %s: retrieved %s symbols (reported count=%s)", exchange, page + 1, len(frame), count)
            if len(frame) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Reached pagination safety limit for {exchange}; refusing incomplete universe.")
    if not frames:
        raise RuntimeError("TradingView returned no US stock symbols.")
    universe = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["name"])
    universe = universe[universe["name"].map(is_equity_symbol)]
    if len(universe) < 1000:
        raise RuntimeError(f"Unexpectedly small universe ({len(universe)}); refusing to publish.")
    LOG.info("Eligible screener symbols after type/symbol filters: %s", len(universe))
    return universe


def is_equity_symbol(symbol: str) -> bool:
    """Conservative symbol-level exclusion for non-common-equity instruments."""
    s = str(symbol).upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\\-]{0,9}", s):
        return False
    # TradingView type=stock already excludes funds. Filter common ETF/fund
    # class patterns and common non-common-equity suffixes as a second guard.
    excluded_suffixes = ("-P", ".PR", "-W", ".WS", "-U", ".UN", "-R")
    if s.endswith(excluded_suffixes):
        return False
    return True


def _fetch_daily(symbols: list[str], exchange: str) -> dict[str, pd.DataFrame]:
    """Download daily history in bounded batches from TradingView screener columns."""
    # The screener's 1D historical fields allow batching multiple symbols.
    # Historical close fields are selected directly from TradingView.
    from tradingview_screener import Query, col

    out: dict[str, pd.DataFrame] = {}
    fields = ["name"] + [f"close|1D[{i}]" for i in range(LOOKBACK + RECENCY + 2)]
    for start in range(0, len(symbols), 50):
        batch = symbols[start:start + 50]
        for symbol in batch:
            q = (
                Query().set_markets("america")
                .select("name", *[f"close|1D[{i}]" for i in range(LOOKBACK + RECENCY + 2)])
                .where(col("name") == symbol, col("exchange") == exchange, col("type") == "stock")
                .limit(1)
            )
            try:
                _, frame = q.get_scanner_data()
                if frame is not None and not frame.empty:
                    row = frame.iloc[0]
                    values = []
                    for i in range(LOOKBACK + RECENCY + 2):
                        value = pd.to_numeric(row.get(f"close|1D[{i}]"), errors="coerce")
                        values.append(float(value) if pd.notna(value) else float("nan"))
                    series = pd.Series(values, dtype="float64").dropna()
                    if len(series) >= MIN_HISTORY:
                        # TradingView history offsets are newest-first; flip to chronological.
                        out[symbol] = pd.DataFrame({"close": series.iloc[::-1].to_numpy()})
            except Exception as exc:
                LOG.warning("History fetch failed for %s: %s", symbol, exc)
        LOG.info("Historical close coverage: %s/%s", len(out), len(symbols))
        time.sleep(0.25)
    return out


def fetch_histories(universe: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Fetch each listing history; grouped by exchange to preserve venue identity."""
    results: dict[str, pd.DataFrame] = {}
    for exchange in EXCHANGES:
        symbols = universe.loc[universe["exchange"] == exchange, "name"].astype(str).tolist()
        LOG.info("Fetching daily history for %s symbols on %s", len(symbols), exchange)
        results.update(_fetch_daily(symbols, exchange))
    coverage = len(results) / max(1, len(universe))
    LOG.info("Historical coverage %.1f%% (%s/%s)", coverage * 100, len(results), len(universe))
    # Strict enough to catch an API schema break, but allows legitimate short-history/newly listed stocks.
    if coverage < 0.90:
        raise RuntimeError(f"Historical data coverage only {coverage:.1%}; refusing to publish incomplete results.")
    return results


def fetch_spy_history() -> pd.Series:
    from tradingview_screener import Query, col
    query = (
        Query().set_markets("america")
        .select("name", *[f"close|1D[{i}]" for i in range(MIN_HISTORY)])
        .where(col("name") == "SPY", col("exchange") == "AMEX", col("type") == "fund")
        .limit(1)
    )
    _, frame = query.get_scanner_data()
    if frame is None or frame.empty:
        # Some TradingView market mappings expose SPY on ARCA.
        query = Query().set_markets("america").select(
            "name", *[f"close|1D[{i}]" for i in range(MIN_HISTORY)]
        ).where(col("name") == "SPY").limit(1)
        _, frame = query.get_scanner_data()
    if frame is None or frame.empty:
        raise RuntimeError("Could not retrieve SPY daily history.")
    row = frame.iloc[0]
    vals = [pd.to_numeric(row.get(f"close|1D[{i}]"), errors="coerce") for i in range(MIN_HISTORY)]
    series = pd.Series(vals, dtype="float64").dropna().iloc[::-1]
    if len(series) < MIN_HISTORY:
        raise RuntimeError(f"SPY history incomplete: {len(series)} bars, expected {MIN_HISTORY}.")
    return series.reset_index(drop=True)


def calculate_signals(universe: pd.DataFrame, histories: dict[str, pd.DataFrame], spy: pd.Series) -> pd.DataFrame:
    records = []
    # The screener exposes historical close values but not necessarily a common
    # timestamp array. Use the same latest N completed daily sessions for all symbols.
    spy = spy.iloc[-MIN_HISTORY:].reset_index(drop=True)
    for symbol, history in histories.items():
        stock = history["close"].iloc[-MIN_HISTORY:].reset_index(drop=True)
        if len(stock) != len(spy):
            continue
        ratio = stock / spy
        if not ratio.notna().all():
            continue
        # For each of the latest RECENCY bars, compare to strictly prior LOOKBACK bars.
        hits = []
        for i in range(max(LOOKBACK, len(ratio) - RECENCY), len(ratio)):
            prior_high = ratio.iloc[i - LOOKBACK:i].max()
            if pd.notna(prior_high) and ratio.iloc[i] > prior_high:
                hits.append((i, float(ratio.iloc[i]), float(prior_high)))
        if not hits:
            continue
        latest_i, latest_rs, previous_high = hits[-1]
        info = universe.loc[universe["name"] == symbol]
        if info.empty:
            continue
        meta = info.iloc[0]
        bars_ago = len(ratio) - 1 - latest_i
        records.append({
            "symbol": symbol,
            "description": str(meta.get("description", "")),
            "exchange": str(meta.get("exchange", "")),
            "breakout_date": f"latest session offset {bars_ago} (historical bar timestamps not exposed)",
            "sessions_ago": bars_ago,
            "rs_ratio": latest_rs,
            "prior_250d_rs_high": previous_high,
            "breakout_pct": (latest_rs / previous_high - 1) * 100,
        })
    result = pd.DataFrame(records)
    if result.empty:
        return pd.DataFrame(columns=["symbol", "description", "exchange", "breakout_date", "sessions_ago", "rs_ratio", "prior_250d_rs_high", "breakout_pct"])
    return result.sort_values(["sessions_ago", "breakout_pct"], ascending=[True, False]).reset_index(drop=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Starting US RS new-high scan (250-session lookback; last 10 completed sessions).")
    universe = get_screener_universe()
    histories = fetch_histories(universe)
    spy = fetch_spy_history()
    hits = calculate_signals(universe, histories, spy)
    OUT.mkdir(exist_ok=True)
    tickers_path = OUT / "rs_new_high_tickers.csv"
    details_path = OUT / "rs_new_high_details.csv"
    hits[["symbol"]].to_csv(tickers_path, index=False)
    hits.to_csv(details_path, index=False)
    logging.info("Completed. Universe=%s; histories=%s; qualifying tickers=%s", len(universe), len(histories), len(hits))
    logging.info("Wrote %s and %s", tickers_path, details_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("Scanner failed; outputs were not updated.")
        raise
