"""Thin wrappers around Twelve Data and Finnhub REST APIs."""

import time
import numpy as np
import requests

import config


class ProviderError(Exception):
    pass


class RateLimiter:
    """Simple pacing so we stay under a provider's requests/minute limit."""

    def __init__(self, min_seconds_between_calls):
        self.min_seconds_between_calls = min_seconds_between_calls
        self._last_call = 0.0

    def wait(self):
        elapsed = time.monotonic() - self._last_call
        remaining = self.min_seconds_between_calls - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_call = time.monotonic()


class TwelveDataClient:
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key):
        self.api_key = api_key
        self._limiter = RateLimiter(config.TWELVEDATA_MIN_SECONDS_BETWEEN_CALLS)

    def _get(self, endpoint, params, retries=2):
        params = dict(params)
        params["apikey"] = self.api_key
        for attempt in range(retries + 1):
            self._limiter.wait()
            try:
                resp = requests.get(f"{self.BASE_URL}/{endpoint}", params=params, timeout=20)
            except requests.RequestException as e:
                raise ProviderError(f"Twelve Data request failed: {e}") from e
            if resp.status_code == 429 and attempt < retries:
                time.sleep(config.TWELVEDATA_MIN_SECONDS_BETWEEN_CALLS * 2)
                continue
            break
        if resp.status_code == 429:
            raise ProviderError("Twelve Data rate limit exceeded (429) after retries")
        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderError(f"Twelve Data returned invalid JSON: {e}") from e
        if isinstance(data, dict) and data.get("status") == "error":
            raise ProviderError(f"Twelve Data error: {data.get('message')}")
        return data

    def get_time_series(self, symbol, outputsize=config.TIME_SERIES_OUTPUTSIZE):
        """Returns dict with ascending-ordered 'dates' (list[str]), 'close' (np.ndarray),
        'volume' (np.ndarray). Returns None if the symbol has no data at all."""
        data = self._get(
            "time_series",
            {"symbol": symbol, "interval": "1day", "outputsize": outputsize},
        )
        values = data.get("values")
        if not values:
            return None
        values = list(reversed(values))  # Twelve Data returns newest-first; we want ascending
        dates = [v["datetime"] for v in values]
        close = np.array([float(v["close"]) for v in values], dtype=float)
        volume = np.array([float(v["volume"]) for v in values], dtype=float)
        return {"dates": dates, "close": close, "volume": volume}

    def get_quote(self, symbol):
        """Returns dict with 'close', 'previous_close', 'volume', or None if unavailable."""
        data = self._get("quote", {"symbol": symbol})
        if not data or "close" not in data:
            return None
        try:
            return {
                "close": float(data["close"]),
                "previous_close": float(data["previous_close"]),
                "volume": float(data.get("volume") or 0),
            }
        except (TypeError, ValueError) as e:
            raise ProviderError(f"Twelve Data quote parse failed for {symbol}: {e}") from e


class FinnhubClient:
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key):
        self.api_key = api_key

    def get_company_news(self, symbol, from_date, to_date):
        """Returns list of dicts with 'datetime' (unix ts), 'headline', 'url'."""
        try:
            resp = requests.get(
                f"{self.BASE_URL}/company-news",
                params={"symbol": symbol, "from": from_date, "to": to_date, "token": self.api_key},
                timeout=20,
            )
        except requests.RequestException as e:
            raise ProviderError(f"Finnhub request failed: {e}") from e
        if resp.status_code != 200:
            raise ProviderError(f"Finnhub returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderError(f"Finnhub returned invalid JSON: {e}") from e
        if not isinstance(data, list):
            raise ProviderError(f"Finnhub returned unexpected payload: {data}")
        return data

    @staticmethod
    def _num_or_none(value):
        return float(value) if isinstance(value, (int, float)) else None

    def get_basic_financials(self, symbol):
        """Returns dict with 'eps_growth_qoq_yoy', 'revenue_growth_qoq_yoy' (decimal
        fractions, e.g. 0.20 = 20%, or None if Finnhub doesn't report the field for this
        symbol), and 'net_margin_current'/'net_margin_prior_quarter' (decimal fractions
        from the quarterly netMargin time series, or None if fewer than 2 quarters exist).

        Finnhub's flat 'metric' fields (epsGrowthQuarterlyYoy, revenueGrowthQuarterlyYoy)
        are percent-scale; the 'series.quarterly.netMargin' time series is already
        decimal-scale. Both are normalized to decimal fractions here.
        """
        try:
            resp = requests.get(
                f"{self.BASE_URL}/stock/metric",
                params={"symbol": symbol, "metric": "all", "token": self.api_key},
                timeout=20,
            )
        except requests.RequestException as e:
            raise ProviderError(f"Finnhub request failed: {e}") from e
        if resp.status_code != 200:
            raise ProviderError(f"Finnhub returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderError(f"Finnhub returned invalid JSON: {e}") from e

        metric = data.get("metric") or {}
        quarterly_net_margin = ((data.get("series") or {}).get("quarterly") or {}).get("netMargin") or []

        eps_growth_pct = self._num_or_none(metric.get("epsGrowthQuarterlyYoy"))
        revenue_growth_pct = self._num_or_none(metric.get("revenueGrowthQuarterlyYoy"))
        margin_now = self._num_or_none(quarterly_net_margin[0]["v"]) if len(quarterly_net_margin) > 0 else None
        margin_prior = self._num_or_none(quarterly_net_margin[1]["v"]) if len(quarterly_net_margin) > 1 else None

        return {
            "eps_growth_qoq_yoy": eps_growth_pct / 100 if eps_growth_pct is not None else None,
            "revenue_growth_qoq_yoy": revenue_growth_pct / 100 if revenue_growth_pct is not None else None,
            "net_margin_current": margin_now,
            "net_margin_prior_quarter": margin_prior,
        }
