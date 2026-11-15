#!/usr/bin/env python3
"""Scan US common stocks and depositary receipts for recent 250-session RS highs vs SPY.

TradingView's public screener is used to discover the eligible universe. Daily
OHLC history comes from Yahoo Finance via yfinance because TradingView's
screener endpoint does not return historical close/time values for the
requested close|1D[n] fields. All stock prices are joined to SPY on exact
calendar dates before relative strength is calculated.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from tradingview_screener import Query, col

LOG = logging.getLogger("rs_scan")
EXCHANGES = ("NASDAQ", "NYSE", "AMEX")
LOOKBACKS = {"3m": 63, "6m": 126, "12m": 250}
LOOKBACK = LOOKBACKS["12m"]  # Backwards-compatible alias for the 12m window.
RECENCY = 10
WEEKLY_LOOKBACK = 250
YAHOO_HISTORY_PERIOD = "10y"
MIN_HISTORY = max(LOOKBACKS.values()) + RECENCY
PAGE_SIZE = 1000
MAX_PAGES = 20
YAHOO_BATCH_SIZE = 300
YAHOO_THREADS = 8
MIN_HISTORY_COVERAGE = 0.70
OUT = Path("outputs")
MIN_PRICE = 10.0
MIN_AVG_DOLLAR_VOLUME_30D = 30_000_000.0
MIN_ADR_PCT = 4.0
MIN_AVG_VOLUME_10D = 350_000
DETAIL_COLUMNS = [
    "symbol", "description", "exchange", "sector", "float_shares_outstanding",
    "breakout_date", "sessions_ago", "close", "adr_pct", "average_volume_10d", "average_dollar_volume_30d",
    "rs_ratio", "prior_250d_rs_high", "breakout_pct",
    "signal_3m", "signal_6m", "signal_12m",
    "breakout_date_3m", "sessions_ago_3m", "rs_ratio_3m", "prior_rs_high_3m", "breakout_pct_3m",
    "breakout_date_6m", "sessions_ago_6m", "rs_ratio_6m", "prior_rs_high_6m", "breakout_pct_6m",
    "breakout_date_12m", "sessions_ago_12m", "rs_ratio_12m", "prior_rs_high_12m", "breakout_pct_12m",
    "weekly_signal", "weekly_rs_date", "weekly_rs_ratio", "weekly_prior_rs_high",
    "weekly_rs_breakout_pct", "weekly_price_high", "weekly_price_vs_high_pct",
]


def _query_with_retry(query: Query, label: str) -> tuple[int, pd.DataFrame]:
    """Call TradingView with bounded retries; reject malformed/empty responses."""
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            count, frame = query.get_scanner_data()
            if frame is None:
                raise RuntimeError("TradingView returned a null data frame")
            return int(count), frame
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                break
            delay = 2 ** attempt
            LOG.warning("%s failed (attempt %s/3): %s; retrying in %ss",
                        label, attempt, exc, delay)
            time.sleep(delay)
    raise RuntimeError(f"{label} failed after 3 attempts: {last_error}") from last_error


def _universe_query(exchange: str, instrument_type: str, offset: int) -> Query:
    # Query.where() replaces the filter list; pass every condition in one call.
    conditions = [
        col("exchange") == exchange,
        col("close") > 0,
        col("type") == instrument_type,
    ]
    if instrument_type == "stock":
        conditions.append(col("typespecs").has(["common"]))
    return (
        Query().set_markets("america")
        .select(
            "name", "description", "exchange", "type", "typespecs", "sector", "industry",
            "float_shares_outstanding", "close", "SMA30", "ADRP", "average_volume_10d_calc",
            "average_volume_30d_calc",
        )
        .where(*conditions)
        .order_by("name")
        .offset(offset)
        # The library's range is [start, end], not [start, row_count].
        .limit(offset + PAGE_SIZE)
    )


def get_screener_universe() -> pd.DataFrame:
    """Retrieve common shares and depositary receipts from the US exchanges."""
    frames: list[pd.DataFrame] = []
    for exchange in EXCHANGES:
        for instrument_type in ("stock", "dr"):
            offset = 0
            total_count: int | None = None
            page = 0
            while page < MAX_PAGES:
                query = _universe_query(exchange, instrument_type, offset)
                count, frame = _query_with_retry(
                    query, f"{exchange}/{instrument_type} page {page + 1}"
                )
                if total_count is None:
                    total_count = count
                    LOG.info("%s %s instruments reported by TradingView: %s",
                             exchange, instrument_type, total_count)
                if frame.empty:
                    if offset < (total_count or 0):
                        raise RuntimeError(
                            f"Incomplete TradingView pagination for {exchange}/{instrument_type}: "
                            f"received no rows at offset {offset} of {total_count}."
                        )
                    break

                frame = frame.reset_index(drop=True)
                if "name" not in frame.columns or "exchange" not in frame.columns:
                    raise RuntimeError(
                        f"Unexpected TradingView columns for {exchange}/{instrument_type}: "
                        f"{list(frame.columns)}"
                    )
                frames.append(frame)
                received = len(frame)
                offset += received
                page += 1
                LOG.info("%s %s page %s: %s rows (%s/%s)",
                         exchange, instrument_type, page, received,
                         offset, total_count)
                if offset >= (total_count or 0):
                    break
                if received < PAGE_SIZE:
                    raise RuntimeError(
                        f"Short TradingView page for {exchange}/{instrument_type}: "
                        f"{received} rows at offset {offset - received}, "
                        f"but totalCount is {total_count}."
                    )
            if page >= MAX_PAGES and offset < (total_count or 0):
                raise RuntimeError(
                    f"Reached pagination safety limit for {exchange}/{instrument_type}; "
                    "refusing to scan an incomplete universe."
                )

    if not frames:
        raise RuntimeError("TradingView returned no US common stocks or depositary receipts.")

    universe = pd.concat(frames, ignore_index=True)
    universe["name"] = universe["name"].astype(str).str.upper().str.strip()
    unexpected_exchanges = sorted(set(universe["exchange"].dropna()) - set(EXCHANGES))
    if unexpected_exchanges:
        raise RuntimeError(
            f"Unexpected exchanges in filtered universe: {unexpected_exchanges}; refusing to publish."
        )
    def is_supported_instrument(row: pd.Series) -> bool:
        if row.get("type") == "dr":
            return True
        specs = row.get("typespecs")
        return row.get("type") == "stock" and isinstance(specs, (list, tuple, set)) and "common" in specs
    if not universe.apply(is_supported_instrument, axis=1).all():
        raise RuntimeError("TradingView returned non-common-stock instruments; refusing to publish.")
    universe = universe[universe["name"].map(is_equity_symbol)]
    universe = universe.drop_duplicates(subset=["name"], keep="first").reset_index(drop=True)
    universe = apply_liquidity_filters(universe)
    if len(universe) < 300:
        raise RuntimeError(
            f"Unexpectedly small price/liquidity-qualified universe ({len(universe)}); refusing to publish."
        )
    LOG.info(
        "Price/liquidity-qualified common stocks and depositary receipts: %s "
        "(price > $%.2f; 30d avg dollar volume > $%.0f; 10d avg volume > %s; ADRP > %.1f%%; biotech included)",
        len(universe), MIN_PRICE, MIN_AVG_DOLLAR_VOLUME_30D,
        MIN_AVG_VOLUME_10D, MIN_ADR_PCT,
    )
    return universe


def apply_liquidity_filters(universe: pd.DataFrame) -> pd.DataFrame:
    """Apply the NEL liquidity thresholds plus the requested $10 share-price floor."""
    required = [
        "close", "SMA30", "ADRP", "average_volume_10d_calc",
        "average_volume_30d_calc",
    ]
    missing = sorted(set(required) - set(universe.columns))
    if missing:
        raise RuntimeError(
            "TradingView did not return required price/liquidity field(s): "
            + ", ".join(missing)
        )

    frame = universe.copy()
    numeric = [
        "close", "SMA30", "ADRP", "average_volume_10d_calc",
        "average_volume_30d_calc",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["average_dollar_volume_30d"] = (
        frame["SMA30"] * frame["average_volume_30d_calc"]
    )
    eligible = (
        (frame["close"] > MIN_PRICE)
        & (frame["SMA30"] > 0)
        & (frame["ADRP"] > MIN_ADR_PCT)
        & (frame["average_volume_10d_calc"] > MIN_AVG_VOLUME_10D)
        & (frame["average_dollar_volume_30d"] > MIN_AVG_DOLLAR_VOLUME_30D)
    )
    return frame.loc[eligible].reset_index(drop=True)


def is_equity_symbol(symbol: str) -> bool:
    """Reject malformed symbols; instrument type filtering excludes funds/preferreds."""
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", str(symbol).upper().strip()))


def yahoo_symbol(symbol: str) -> str:
    """Translate TradingView class-share dots to Yahoo Finance hyphen notation."""
    return str(symbol).upper().strip().replace(".", "-")


def _normalise_daily_series(series: pd.Series) -> pd.Series:
    result = pd.to_numeric(series, errors="coerce").dropna().copy()
    index = pd.DatetimeIndex(pd.to_datetime(result.index))
    if index.tz is not None:
        index = index.tz_convert(None)
    result.index = index.normalize()
    result = result[~result.index.duplicated(keep="last")].sort_index()
    result.name = "close"
    return result


def _extract_close(data: pd.DataFrame, ticker: str) -> pd.Series:
    """Extract one ticker's Close series from yfinance's single/multi-ticker shapes."""
    if data is None or data.empty:
        return pd.Series(dtype="float64")
    if isinstance(data.columns, pd.MultiIndex):
        level0 = data.columns.get_level_values(0)
        level1 = data.columns.get_level_values(1)
        if ticker in level0:
            series = data[ticker]["Close"]
        elif "Close" in level0 and ticker in level1:
            series = data["Close"][ticker]
        else:
            return pd.Series(dtype="float64")
    else:
        if "Close" not in data.columns:
            return pd.Series(dtype="float64")
        series = data["Close"]
    return _normalise_daily_series(series)


def _download_yahoo_batch(symbols: list[str], batch_number: int) -> pd.DataFrame:
    """Download a batch with one retry for transient Yahoo failures."""
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            frame = yf.download(
                tickers=symbols,
                period=YAHOO_HISTORY_PERIOD,
                interval="1d",
                auto_adjust=False,
                actions=False,
                group_by="ticker",
                threads=YAHOO_THREADS,
                progress=False,
                timeout=30,
            )
            if frame is not None and not frame.empty:
                return frame
            last_error = RuntimeError("Yahoo Finance returned an empty batch")
        except Exception as exc:
            last_error = exc
        if attempt < 3:
            delay = 2 ** attempt
            LOG.warning("Yahoo batch %s failed (attempt %s/3): %s; retrying in %ss",
                        batch_number, attempt, last_error, delay)
            time.sleep(delay)
    LOG.error("Yahoo batch %s failed after retries: %s", batch_number, last_error)
    return pd.DataFrame()


def fetch_histories(universe: pd.DataFrame) -> dict[str, pd.Series]:
    """Fetch up to ten years of daily closes for daily and 250-week RS analysis."""
    tv_to_yahoo = {
        row["name"]: yahoo_symbol(row["name"])
        for _, row in universe.iterrows()
    }
    yahoo_symbols = list(dict.fromkeys(tv_to_yahoo.values()))
    histories: dict[str, pd.Series] = {}
    total_batches = math.ceil(len(yahoo_symbols) / YAHOO_BATCH_SIZE)

    for batch_idx, start in enumerate(range(0, len(yahoo_symbols), YAHOO_BATCH_SIZE), start=1):
        batch = yahoo_symbols[start:start + YAHOO_BATCH_SIZE]
        LOG.info("Downloading Yahoo daily history batch %s/%s (%s symbols)",
                 batch_idx, total_batches, len(batch))
        data = _download_yahoo_batch(batch, batch_idx)
        batch_found = 0
        for tv_symbol, yf_symbol in tv_to_yahoo.items():
            if yf_symbol not in batch:
                continue
            close = _extract_close(data, yf_symbol)
            if len(close) >= MIN_HISTORY:
                histories[tv_symbol] = close
                batch_found += 1
        LOG.info("Yahoo history coverage after batch %s: %s/%s symbols",
                 batch_idx, len(histories), len(universe))
        # Short pause reduces the chance of upstream throttling between batches.
        time.sleep(0.5)

    coverage = len(histories) / max(1, len(universe))
    LOG.info("Yahoo history coverage: %.1f%% (%s/%s)",
             coverage * 100, len(histories), len(universe))
    if coverage < MIN_HISTORY_COVERAGE:
        raise RuntimeError(
            f"Yahoo daily history coverage only {coverage:.1%}; "
            f"minimum required is {MIN_HISTORY_COVERAGE:.0%}. Refusing to publish."
        )
    return histories


def fetch_spy_history() -> pd.Series:
    """Fetch SPY daily closes, which provide the canonical US trading-session index."""
    data = yf.download(
        tickers="SPY",
        period=YAHOO_HISTORY_PERIOD,
        interval="1d",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=False,
        timeout=30,
    )
    spy = _extract_close(data, "SPY")
    if len(spy) < MIN_HISTORY:
        raise RuntimeError(f"SPY history incomplete: {len(spy)} bars, expected at least {MIN_HISTORY}.")
    # Scheduled scans run at 00:40 UTC. If manually run before 22:00 UTC,
    # discard today's potentially incomplete daily candle.
    now = datetime.now(timezone.utc)
    cutoff = pd.Timestamp(now.date() if now.hour >= 22 else (now.date() - pd.Timedelta(days=1)))
    spy = spy.loc[spy.index <= cutoff]
    if len(spy) < MIN_HISTORY:
        raise RuntimeError(f"SPY completed daily history incomplete after cutoff: {len(spy)} bars.")
    return spy


def calculate_weekly_lead_signal(stock: pd.Series, spy: pd.Series) -> dict | None:
    """Track a 250-week RS breakout until price itself makes a 250-week high.

    A stock qualifies when its weekly RS makes a fresh 250-week high while
    its weekly close is still below its own prior 250-week price high. The
    signal persists across subsequent completed weeks and is invalidated by
    the first later week in which price makes a fresh 250-week high.
    """
    stock = _normalise_daily_series(stock)
    spy = _normalise_daily_series(spy)
    aligned = pd.concat([stock.rename("stock"), spy.rename("spy")], axis=1).dropna()
    if aligned.empty:
        return None

    weekly = aligned.resample("W-FRI").last().dropna()
    # Only include fully completed Friday-ending weeks.
    latest_session = aligned.index.max()
    last_completed_friday = latest_session - pd.Timedelta(
        days=(latest_session.weekday() - 4) % 7
    )
    weekly = weekly.loc[weekly.index <= last_completed_friday]
    if len(weekly) < WEEKLY_LOOKBACK + 1:
        return None

    rs = weekly["stock"] / weekly["spy"]
    prior_rs_high = rs.shift(1).rolling(
        WEEKLY_LOOKBACK, min_periods=WEEKLY_LOOKBACK
    ).max()
    prior_price_high = weekly["stock"].shift(1).rolling(
        WEEKLY_LOOKBACK, min_periods=WEEKLY_LOOKBACK
    ).max()

    valid_history = prior_rs_high.notna() & prior_price_high.notna()
    rs_lead_events = (
        valid_history
        & rs.gt(prior_rs_high)
        & weekly["stock"].lt(prior_price_high)
    )
    price_break_events = valid_history & weekly["stock"].gt(prior_price_high)

    rs_positions = [i for i, hit in enumerate(rs_lead_events.tolist()) if bool(hit)]
    if not rs_positions:
        return None
    latest_rs_position = rs_positions[-1]
    price_positions = [i for i, hit in enumerate(price_break_events.tolist()) if bool(hit)]
    latest_price_position = price_positions[-1] if price_positions else -1

    # The latest qualifying RS breakout must have happened after the latest
    # 250-week price breakout. This keeps the signal alive until price catches up.
    if latest_rs_position <= latest_price_position:
        return None

    current_rs = float(rs.iloc[latest_rs_position])
    previous_rs_high = float(prior_rs_high.iloc[latest_rs_position])
    current_price = float(weekly["stock"].iloc[-1])
    latest_price_high = float(prior_price_high.iloc[-1])
    return {
        "date": weekly.index[latest_rs_position].strftime("%Y-%m-%d"),
        "rs_ratio": current_rs,
        "prior_rs_high": previous_rs_high,
        "rs_breakout_pct": (current_rs / previous_rs_high - 1) * 100,
        "price_high": latest_price_high,
        "price_vs_high_pct": (current_price / latest_price_high - 1) * 100,
    }

def calculate_signals(
    universe: pd.DataFrame, histories: dict[str, pd.Series], spy: pd.Series,
) -> pd.DataFrame:
    """Calculate 3m, 6m and 12m RS highs on SPY sessions."""
    spy = _normalise_daily_series(spy)
    records: list[dict] = []
    for symbol, stock_close in histories.items():
        stock = _normalise_daily_series(stock_close).reindex(spy.index)
        ratio = stock / spy
        signals: dict[str, dict | None] = {}
        for label, lookback in LOOKBACKS.items():
            prior_high = ratio.shift(1).rolling(lookback, min_periods=lookback).max()
            recent = ratio.gt(prior_high).iloc[-RECENCY:]
            hit_positions = [i for i, hit in enumerate(recent.tolist()) if bool(hit)]
            if not hit_positions:
                signals[label] = None
                continue
            latest_position = len(spy) - RECENCY + hit_positions[-1]
            latest_rs = float(ratio.iloc[latest_position])
            previous_high = float(prior_high.iloc[latest_position])
            signals[label] = {
                "date": spy.index[latest_position].strftime("%Y-%m-%d"),
                "sessions_ago": len(spy) - 1 - latest_position,
                "rs_ratio": latest_rs, "prior_high": previous_high,
                "breakout_pct": (latest_rs / previous_high - 1) * 100,
            }
        weekly_signal = calculate_weekly_lead_signal(stock, spy)
        if not any(signals.values()) and weekly_signal is None:
            continue
        info = universe.loc[universe["name"] == symbol]
        if info.empty:
            continue
        meta = info.iloc[0]
        daily_signals = [v for v in signals.values() if v]
        primary = signals["12m"] or (min(daily_signals, key=lambda v: v["sessions_ago"]) if daily_signals else {
            "date": weekly_signal["date"], "sessions_ago": 0,
            "rs_ratio": float("nan"), "breakout_pct": float("nan"),
        })
        record = {
            "symbol": symbol, "description": str(meta.get("description", "")),
            "exchange": str(meta.get("exchange", "")), "sector": str(meta.get("sector", "") or ""),
            "float_shares_outstanding": float(pd.to_numeric(meta.get("float_shares_outstanding"), errors="coerce")),
            "breakout_date": primary["date"], "close": float(meta.get("close", float("nan"))),
            "adr_pct": float(meta.get("ADRP", float("nan"))),
            "average_volume_10d": float(meta.get("average_volume_10d_calc", float("nan"))),
            "average_dollar_volume_30d": float(meta.get("average_dollar_volume_30d", float("nan"))),
            "sessions_ago": primary["sessions_ago"], "rs_ratio": primary["rs_ratio"],
            "prior_250d_rs_high": signals["12m"]["prior_high"] if signals["12m"] else float("nan"),
            "breakout_pct": primary["breakout_pct"],
        }
        record.update({
            "weekly_signal": weekly_signal is not None,
            "weekly_rs_date": weekly_signal["date"] if weekly_signal else "",
            "weekly_rs_ratio": weekly_signal["rs_ratio"] if weekly_signal else float("nan"),
            "weekly_prior_rs_high": weekly_signal["prior_rs_high"] if weekly_signal else float("nan"),
            "weekly_rs_breakout_pct": weekly_signal["rs_breakout_pct"] if weekly_signal else float("nan"),
            "weekly_price_high": weekly_signal["price_high"] if weekly_signal else float("nan"),
            "weekly_price_vs_high_pct": weekly_signal["price_vs_high_pct"] if weekly_signal else float("nan"),
        })
        for label, signal in signals.items():
            record["signal_" + label] = signal is not None
            record["breakout_date_" + label] = signal["date"] if signal else ""
            record["sessions_ago_" + label] = signal["sessions_ago"] if signal else float("nan")
            record["rs_ratio_" + label] = signal["rs_ratio"] if signal else float("nan")
            record["prior_rs_high_" + label] = signal["prior_high"] if signal else float("nan")
            record["breakout_pct_" + label] = signal["breakout_pct"] if signal else float("nan")
        records.append(record)
    if not records:
        return pd.DataFrame(columns=DETAIL_COLUMNS)
    return pd.DataFrame(records, columns=DETAIL_COLUMNS).sort_values(["sessions_ago", "breakout_pct"], ascending=[True, False]).reset_index(drop=True)

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Starting US RS scan (63/126/250-session daily RS highs plus 52-week weekly RS-leading-price signals).")
    universe = get_screener_universe()
    histories = fetch_histories(universe)
    spy = fetch_spy_history()
    hits = calculate_signals(universe, histories, spy)

    OUT.mkdir(exist_ok=True)
    tickers_path = OUT / "rs_new_high_tickers.csv"
    details_path = OUT / "rs_new_high_details.csv"
    hits[["symbol"]].to_csv(tickers_path, index=False)
    hits.to_csv(details_path, index=False)

    # Keep an immutable-by-date snapshot of each completed trading session.
    # Re-running a scan for the same session refreshes that session's snapshot.
    history_dir = OUT / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    session_dates = [date.strftime("%Y-%m-%d") for date in spy.index[-RECENCY:]]
    snapshot_date = session_dates[-1]
    dated_details = history_dir / f"rs_new_high_details_{snapshot_date}.csv"
    dated_tickers = history_dir / f"rs_new_high_tickers_{snapshot_date}.csv"
    hits.to_csv(dated_details, index=False)
    hits[["symbol"]].to_csv(dated_tickers, index=False)

    available_dates = sorted(
        path.name.removeprefix("rs_new_high_details_").removesuffix(".csv")
        for path in history_dir.glob("rs_new_high_details_????-??-??.csv")
    )
    manifest = {
        "latest_date": snapshot_date,
        "dates": available_dates,
        "sessions": session_dates,
        "details_pattern": "rs_new_high_details_{date}.csv",
        "tickers_pattern": "rs_new_high_tickers_{date}.csv",
    }
    (history_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    LOG.info(
        "Completed. Universe=%s; histories=%s; qualifying tickers=%s; last SPY session=%s",
        len(universe), len(histories), len(hits), snapshot_date,
    )
    LOG.info("Wrote current outputs plus dated snapshot %s", dated_details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
