"""Signal definitions: trend template, RS, crosses, volume breakout, move/gap."""

import numpy as np
import talib

import config


def _align_by_date(dates_a, values_a, dates_b, values_b):
    """Intersect two (dates, values) series on shared dates, both ascending, returns
    two aligned numpy arrays in date order."""
    index_b = {d: v for d, v in zip(dates_b, values_b)}
    a_out, b_out = [], []
    for d, v in zip(dates_a, values_a):
        if d in index_b:
            a_out.append(v)
            b_out.append(index_b[d])
    return np.array(a_out, dtype=float), np.array(b_out, dtype=float)


def compute_rs_series(aligned_closes, aligned_bench_closes, lookback=config.RS_LOOKBACK):
    """RS = (price_t / price_t-lookback) / (bench_t / bench_t-lookback) - 1, per point."""
    n = len(aligned_closes)
    rs = np.full(n, np.nan)
    for i in range(lookback, n):
        bench_prior = aligned_bench_closes[i - lookback]
        bench_now = aligned_bench_closes[i]
        stock_prior = aligned_closes[i - lookback]
        if bench_prior == 0 or stock_prior == 0:
            continue
        stock_ratio = aligned_closes[i] / stock_prior
        bench_ratio = bench_now / bench_prior
        if bench_ratio == 0:
            continue
        rs[i] = stock_ratio / bench_ratio - 1
    return rs


def evaluate_trend_template(series, bench_series):
    """series/bench_series: dicts from providers.get_time_series (ascending dates).
    Returns dict with at least 'status' in {"green", "red", "grey"}."""
    dates, close = series["dates"], series["close"]

    if len(close) < config.MIN_HISTORY_DAYS:
        return {"status": "grey", "reason": "insufficient_history", "history_days": len(close)}

    sma50 = talib.SMA(close, timeperiod=50)
    sma150 = talib.SMA(close, timeperiod=150)
    sma200 = talib.SMA(close, timeperiod=200)

    lookback = config.SMA200_TREND_LOOKBACK
    if len(sma200) <= lookback or np.isnan(sma200[-1]) or np.isnan(sma200[-1 - lookback]):
        return {"status": "grey", "reason": "insufficient_history", "history_days": len(close)}

    price = close[-1]
    cond_price_above = price > sma50[-1] and price > sma150[-1] and price > sma200[-1]
    cond_order = sma50[-1] > sma150[-1] > sma200[-1]
    cond_sma200_rising = sma200[-1] > sma200[-1 - lookback]

    aligned_close, aligned_bench = _align_by_date(dates, close, bench_series["dates"], bench_series["close"])
    rs_series = compute_rs_series(aligned_close, aligned_bench)
    rs_trend_lb = config.RS_TREND_LOOKBACK
    cond_rs = False
    rs_today = rs_trend = None
    if len(rs_series) > rs_trend_lb:
        rs_today_val = rs_series[-1]
        rs_ago_val = rs_series[-1 - rs_trend_lb]
        if not (np.isnan(rs_today_val) or np.isnan(rs_ago_val)):
            rs_today, rs_trend = float(rs_today_val), float(rs_today_val - rs_ago_val)
            cond_rs = rs_today > 0 and rs_trend > 0

    is_green = cond_price_above and cond_order and cond_sma200_rising and cond_rs
    return {
        "status": "green" if is_green else "red",
        "price": float(price),
        "sma50": float(sma50[-1]),
        "sma150": float(sma150[-1]),
        "sma200": float(sma200[-1]),
        "rs": rs_today,
        "rs_trend": rs_trend,
        "conditions": {
            "price_above_smas": bool(cond_price_above),
            "sma_order": bool(cond_order),
            "sma200_rising": bool(cond_sma200_rising),
            "rs_positive_and_rising": bool(cond_rs),
        },
    }


def detect_cross(series):
    """Golden/death cross on the last two daily bars. Returns 'golden_cross', 'death_cross'
    or None. Needs >=200 bars since it relies on SMA200."""
    close = series["close"]
    if len(close) < config.MIN_HISTORY_DAYS + 1:
        return None
    sma50 = talib.SMA(close, timeperiod=50)
    sma200 = talib.SMA(close, timeperiod=200)
    if np.isnan(sma50[-2]) or np.isnan(sma200[-2]) or np.isnan(sma50[-1]) or np.isnan(sma200[-1]):
        return None
    diff_yesterday = sma50[-2] - sma200[-2]
    diff_today = sma50[-1] - sma200[-1]
    if diff_yesterday <= 0 and diff_today > 0:
        return "golden_cross"
    if diff_yesterday >= 0 and diff_today < 0:
        return "death_cross"
    return None


def compute_volume_avg20(series):
    """Mean of the 20 trading days before today (today excluded). None if not enough data."""
    volume = series["volume"]
    if len(volume) < 21:
        return None
    avg20 = float(np.mean(volume[-21:-1]))
    return avg20 if avg20 > 0 else None


def detect_volume_breakout_full(series):
    """Full-run version: today's volume vs the prior-20-days average, both from daily history.
    Returns (breakout: bool, ratio: float|None, avg20: float|None)."""
    avg20 = compute_volume_avg20(series)
    if avg20 is None:
        return False, None, None
    today_volume = float(series["volume"][-1])
    ratio = today_volume / avg20
    return ratio >= config.VOLUME_MULTIPLIER, ratio, avg20


def detect_volume_breakout_intraday(current_volume, cached_avg20):
    """Intraday version: live cumulative session volume vs cached avg20 from the last full run."""
    if not cached_avg20:
        return False, None
    ratio = current_volume / cached_avg20
    return ratio >= config.VOLUME_MULTIPLIER, ratio


def detect_move_full(series):
    """Full-run version: today's close vs yesterday's close from daily history."""
    close = series["close"]
    if len(close) < 2 or close[-2] == 0:
        return False, None
    move_pct = abs(close[-1] - close[-2]) / close[-2]
    return move_pct >= config.MOVE_THRESHOLD, move_pct


def detect_move_intraday(quote):
    """Intraday version: live quote close vs previous_close."""
    prev = quote["previous_close"]
    if prev == 0:
        return False, None
    move_pct = abs(quote["close"] - prev) / prev
    return move_pct >= config.MOVE_THRESHOLD, move_pct
