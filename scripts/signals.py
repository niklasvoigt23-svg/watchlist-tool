"""Signal definitions: Trendstruktur, Relative Staerke, Fundamental, Crosses,
Volume-Breakout, Move/Gap, 52-Wochen-Hoch."""

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


def evaluate_trendstruktur(series):
    """series: dict from providers.get_time_series (ascending dates).
    Returns dict with at least 'status' in {"green", "red", "grey"}."""
    close = series["close"]

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

    is_green = cond_price_above and cond_order and cond_sma200_rising
    return {
        "status": "green" if is_green else "red",
        "price": float(price),
        "sma50": float(sma50[-1]),
        "sma150": float(sma150[-1]),
        "sma200": float(sma200[-1]),
        "conditions": {
            "price_above_smas": bool(cond_price_above),
            "sma_order": bool(cond_order),
            "sma200_rising": bool(cond_sma200_rising),
        },
    }


def evaluate_relative_staerke(series, bench_series):
    """Independent of Trendstruktur/SMA200 -- only needs RS_LOOKBACK + RS_TREND_LOOKBACK
    trading days, so it can turn green/red well before Trendstruktur has enough history."""
    dates, close = series["dates"], series["close"]

    if len(close) < config.MIN_HISTORY_DAYS_RS:
        return {"status": "grey", "reason": "insufficient_history", "history_days": len(close)}

    aligned_close, aligned_bench = _align_by_date(dates, close, bench_series["dates"], bench_series["close"])
    rs_series = compute_rs_series(aligned_close, aligned_bench)
    rs_trend_lb = config.RS_TREND_LOOKBACK
    if len(rs_series) <= rs_trend_lb:
        return {"status": "grey", "reason": "insufficient_history", "history_days": len(close)}

    rs_today_val = rs_series[-1]
    rs_ago_val = rs_series[-1 - rs_trend_lb]
    if np.isnan(rs_today_val) or np.isnan(rs_ago_val):
        return {"status": "grey", "reason": "insufficient_history", "history_days": len(close)}

    rs_today, rs_trend = float(rs_today_val), float(rs_today_val - rs_ago_val)
    is_green = rs_today > 0 and rs_trend > 0
    return {
        "status": "green" if is_green else "red",
        "rs": rs_today,
        "rs_trend": rs_trend,
        "conditions": {"rs_positive_and_rising": bool(is_green)},
    }


def evaluate_fundamental(financials):
    """financials: dict from FinnhubClient.get_basic_financials, or None on provider error.
    Grey (not red) whenever any of the three required fields is missing -- common for
    loss-making or thinly-covered micro-caps, per the "technical gap != red" convention."""
    if financials is None:
        return {"status": "grey", "reason": "no_data"}

    eps_growth = financials.get("eps_growth_qoq_yoy")
    revenue_growth = financials.get("revenue_growth_qoq_yoy")
    margin_now = financials.get("net_margin_current")
    margin_prior = financials.get("net_margin_prior_quarter")

    if eps_growth is None or revenue_growth is None or margin_now is None or margin_prior is None:
        return {
            "status": "grey",
            "reason": "insufficient_fundamentals",
            "eps_growth": eps_growth,
            "revenue_growth": revenue_growth,
            "net_margin_current": margin_now,
            "net_margin_prior_quarter": margin_prior,
        }

    cond_eps = eps_growth >= config.EPS_GROWTH_MIN
    cond_revenue = revenue_growth >= config.REVENUE_GROWTH_MIN
    cond_margin = margin_now >= margin_prior
    is_green = cond_eps and cond_revenue and cond_margin
    return {
        "status": "green" if is_green else "red",
        "eps_growth": eps_growth,
        "revenue_growth": revenue_growth,
        "net_margin_current": margin_now,
        "net_margin_prior_quarter": margin_prior,
        "conditions": {
            "eps_growth_ok": bool(cond_eps),
            "revenue_growth_ok": bool(cond_revenue),
            "margin_not_declining": bool(cond_margin),
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


def compute_prior_year_high(series, lookback=config.YEAR_HIGH_LOOKBACK):
    """Highest close over the `lookback` trading days before today (today excluded).
    None if not enough history yet."""
    close = series["close"]
    if len(close) < lookback + 1:
        return None
    return float(np.max(close[-(lookback + 1):-1]))


def detect_year_high_breakout_full(series):
    """Full-run version: today's close vs the highest close of the prior `lookback` days.
    Returns (breakout: bool, pct_above: float|None, prior_high: float|None)."""
    prior_high = compute_prior_year_high(series)
    if prior_high is None:
        return False, None, None
    price = float(series["close"][-1])
    breakout = price > prior_high
    pct_above = (price / prior_high - 1) * 100 if breakout else None
    return breakout, pct_above, prior_high


def detect_year_high_breakout_intraday(price, cached_prior_high):
    """Intraday version: live quote price vs the prior_high cached from the last full run."""
    if not cached_prior_high:
        return False, None
    breakout = price > cached_prior_high
    pct_above = (price / cached_prior_high - 1) * 100 if breakout else None
    return breakout, pct_above


# --- RVOL-Tier: Volumen-Rating an jedem Einzelereignis-Signal (v3) ---

def compute_rvol(current_volume, avg20):
    if not avg20:
        return None
    return current_volume / avg20


def rvol_tier(ratio):
    if ratio is None:
        return None
    if ratio < config.RVOL_DEAD_MAX:
        return "Dead"
    if ratio < config.RVOL_BELOW_AVG_MAX:
        return "Below Avg"
    if ratio < config.RVOL_ABOVE_AVG_MAX:
        return "Above Avg"
    if ratio < config.RVOL_HIGH_MAX:
        return "High"
    return "Extreme"


# --- Neue Einzelereignis-Signale (v3), alle nur im vollen Lauf (brauchen taegliche OHLCV-Historie) ---

def detect_pocket_pivot(series):
    """Up-Tag mit Volumen > groesstem Down-Tag-Volumen der letzten
    POCKET_PIVOT_DOWNDAY_LOOKBACK Handelstage, zusaetzlich Close > SMA50.
    Gibt es in diesem Fenster keinen einzigen Down-Tag, ist die Bedingung nicht
    auswertbar (kein Vergleichswert) -- das Signal feuert dann bewusst nicht,
    statt vacuously wahr zu sein."""
    close = series["close"]
    volume = series["volume"]
    lookback = config.POCKET_PIVOT_DOWNDAY_LOOKBACK
    min_len = max(config.POCKET_PIVOT_MIN_SMA, lookback) + 2
    if len(close) < min_len:
        return False

    if close[-1] <= close[-2]:
        return False

    sma = talib.SMA(close, timeperiod=config.POCKET_PIVOT_MIN_SMA)
    if np.isnan(sma[-1]) or close[-1] <= sma[-1]:
        return False

    down_day_volumes = [
        volume[i] for i in range(-(lookback + 1), -1) if close[i] < close[i - 1]
    ]
    if not down_day_volumes:
        return False
    return bool(volume[-1] > max(down_day_volumes))


def compute_narrow_range_flags(series):
    """NR4/NR7 (Toby Crabel): heutige Tagesspanne ist die kleinste der letzten 4 bzw. 7
    Handelstage inklusive heute. Reine Dashboard-Markierung, kein Telegram-Alarm."""
    high, low = series["high"], series["low"]
    result = {"nr4": False, "nr7": False}
    if len(high) >= config.NR4_LOOKBACK:
        ranges = high[-config.NR4_LOOKBACK:] - low[-config.NR4_LOOKBACK:]
        result["nr4"] = bool(ranges[-1] <= np.min(ranges))
    if len(high) >= config.NR7_LOOKBACK:
        ranges = high[-config.NR7_LOOKBACK:] - low[-config.NR7_LOOKBACK:]
        result["nr7"] = bool(ranges[-1] <= np.min(ranges))
    return result


def detect_aroon_crossover(series):
    """Aroon(25): bullischer Crossover wenn Aroon-Up von unten nach oben ueber Aroon-Down
    kreuzt UND dabei > AROON_CROSS_THRESHOLD liegt (und spiegelbildlich fuer bearisch).
    Returns 'bullish', 'bearish' oder None."""
    high, low = series["high"], series["low"]
    period = config.AROON_PERIOD
    if len(high) < period + 2:
        return None
    aroondown, aroonup = talib.AROON(high, low, timeperiod=period)
    if np.isnan(aroonup[-2]) or np.isnan(aroondown[-2]) or np.isnan(aroonup[-1]) or np.isnan(aroondown[-1]):
        return None

    up_prev, down_prev = aroonup[-2], aroondown[-2]
    up_now, down_now = aroonup[-1], aroondown[-1]
    threshold = config.AROON_CROSS_THRESHOLD

    if up_prev <= down_prev and up_now > down_now and up_now > threshold:
        return "bullish"
    if down_prev <= up_prev and down_now > up_now and down_now > threshold:
        return "bearish"
    return None


def detect_ttm_squeeze_fire(series):
    """TTM Squeeze (John Carter): Bollinger Bands(20,2) vollstaendig innerhalb des Keltner
    Channel(EMA20 +/- 1.5*ATR20) = Squeeze-on. Feuert beim Uebergang Squeeze-on -> Squeeze-off
    am aktuellen Tag. Richtung: bullisch wenn Close ueber der Bollinger-Mittellinie, sonst
    bearisch. Returns 'bullish', 'bearish' oder None."""
    close, high, low = series["close"], series["high"], series["low"]
    needed = max(config.BB_PERIOD, config.KELTNER_EMA_PERIOD, config.KELTNER_ATR_PERIOD) + 2
    if len(close) < needed:
        return None

    upper_bb, mid_bb, lower_bb = talib.BBANDS(
        close, timeperiod=config.BB_PERIOD, nbdevup=config.BB_STDDEV, nbdevdn=config.BB_STDDEV
    )
    kc_mid = talib.EMA(close, timeperiod=config.KELTNER_EMA_PERIOD)
    atr = talib.ATR(high, low, close, timeperiod=config.KELTNER_ATR_PERIOD)
    kc_upper = kc_mid + config.KELTNER_ATR_MULTIPLIER * atr
    kc_lower = kc_mid - config.KELTNER_ATR_MULTIPLIER * atr

    def squeeze_on(i):
        values = (upper_bb[i], lower_bb[i], kc_upper[i], kc_lower[i])
        if any(np.isnan(v) for v in values):
            return None
        return upper_bb[i] < kc_upper[i] and lower_bb[i] > kc_lower[i]

    on_prev = squeeze_on(-2)
    on_now = squeeze_on(-1)
    if on_prev is None or on_now is None:
        return None
    if on_prev and not on_now:
        return "bullish" if close[-1] > mid_bb[-1] else "bearish"
    return None
