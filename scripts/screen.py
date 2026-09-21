"""Main entry point. Run modes:

  python screen.py --mode full      -> alle Kriterien, einmal taeglich nach US-Handelsschluss
  python screen.py --mode intraday  -> nur Volumen-Breakout, Move/Gap, 52-Wochen-Hoch, News

Reads data/watchlist.csv + data/state.json, writes data/state.json + docs/results.json,
sends a bundled Telegram message for any new (non-duplicate) alerts.
"""

import argparse
import csv
import datetime
import json
import os
import sys

import config
import signals
import telegram
from providers import FinnhubClient, ProviderError, TwelveDataClient


def load_watchlist(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row for row in reader if row.get("ticker")]


def migrate_state_entry(entry):
    """One-time migration: the old single trend_template_status becomes the starting
    value for both new, independent ampeln. Recalculated fresh on the next full run either
    way, this is just a reasonable starting point instead of grey until then."""
    if "trend_template_status" in entry:
        old_status = entry.pop("trend_template_status")
        entry.setdefault("trendstruktur_status", old_status)
        entry.setdefault("relative_staerke_status", old_status)
    entry.pop("trend_template_reason", None)
    return entry


def load_state(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    return {ticker: migrate_state_entry(entry) for ticker, entry in state.items()}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def save_results(path, results, mode, run_timestamp):
    payload = {
        "last_run_at": run_timestamp,
        "last_run_mode": mode,
        "tickers": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


STATUS_LABELS = {
    "trendstruktur_status": "Trendstruktur",
    "relative_staerke_status": "Relative Staerke",
    "fundamental_status": "Fundamental",
}


def fmt_status_change(ticker, label, status):
    icon = "\U0001F7E2" if status == "green" else "\U0001F534"
    verb = "GRUEN" if status == "green" else "ROT"
    return f"{icon} {ticker}: {label} {verb}"


def fmt_cross(ticker, cross_type):
    if cross_type == "golden_cross":
        return f"\U0001F7E2 {ticker}: Golden Cross (SMA50 > SMA200)"
    return f"\U0001F534 {ticker}: Death Cross (SMA50 < SMA200)"


def fmt_volume(ticker, ratio):
    return f"\U0001F7E1 {ticker}: Volumen-Breakout ({ratio:.1f}x 20-Tage-Durchschnitt)"


def fmt_move(ticker, move_pct, direction_sign):
    sign = "+" if direction_sign >= 0 else "-"
    return f"\U0001F7E1 {ticker}: Kursbewegung {sign}{move_pct * 100:.1f}% ggue. letztem Schlusskurs"


def fmt_year_high(ticker, pct_above):
    if pct_above is not None:
        return f"\U0001F7E1 {ticker}: Ausbruch ueber 52-Wochen-Hoch (+{pct_above:.1f}%)"
    return f"\U0001F7E1 {ticker}: Ausbruch ueber 52-Wochen-Hoch"


def fmt_news(ticker, item):
    headline = item.get("headline", "").strip()
    url = item.get("url", "")
    return f"\U0001F4F0 {ticker}: {headline} {url}".strip()


def today_str():
    return datetime.date.today().isoformat()


def process_news(ticker, entry_state, new_state, fh_client, alerts):
    if not (config.ENABLE_NEWS and fh_client):
        return
    to_date = datetime.date.today()
    from_date = to_date - datetime.timedelta(days=config.NEWS_LOOKBACK_DAYS)
    try:
        items = fh_client.get_company_news(ticker, from_date.isoformat(), to_date.isoformat())
    except ProviderError as e:
        print(f"[warn] Finnhub news failed for {ticker}: {e}", file=sys.stderr)
        return
    last_seen = entry_state.get("news_last_seen_ts", 0)
    new_items = [n for n in items if isinstance(n.get("datetime"), (int, float)) and n["datetime"] > last_seen]
    if not new_items:
        return
    new_items.sort(key=lambda n: n["datetime"])
    for item in new_items:
        alerts.append(fmt_news(ticker, item))
    new_state["news_last_seen_ts"] = max(n["datetime"] for n in items) if items else last_seen


def apply_status_alerts(ticker, entry_state, new_state, results_out, alerts):
    """Compares each of the three independent ampeln to its previous state and appends
    a status-change alert (only) when it actually flipped between green and red."""
    for status_key, status_result in results_out.items():
        label = STATUS_LABELS[status_key]
        prev_status = entry_state.get(status_key)
        if status_result["status"] in ("green", "red"):
            new_state[status_key] = status_result["status"]
            if prev_status in ("green", "red") and prev_status != status_result["status"]:
                alerts.append(fmt_status_change(ticker, label, status_result["status"]))


def run_full(ticker, entry_state, td_client, fh_client, spy_series, alerts):
    """Returns (result_dict, new_state_dict)."""
    series = td_client.get_time_series(ticker)
    new_state = dict(entry_state)
    if series is None or len(series["close"]) == 0:
        return {"data_status": "no_data"}, new_state

    trendstruktur = signals.evaluate_trendstruktur(series)
    relative_staerke = signals.evaluate_relative_staerke(series, spy_series)

    financials = None
    if fh_client:
        try:
            financials = fh_client.get_basic_financials(ticker)
        except ProviderError as e:
            print(f"[warn] {ticker}: Finnhub basic-financials failed: {e}", file=sys.stderr)
    fundamental = signals.evaluate_fundamental(financials)

    apply_status_alerts(
        ticker, entry_state, new_state,
        {
            "trendstruktur_status": trendstruktur,
            "relative_staerke_status": relative_staerke,
            "fundamental_status": fundamental,
        },
        alerts,
    )

    cross = signals.detect_cross(series)
    vol_breakout, vol_ratio, vol_avg20 = signals.detect_volume_breakout_full(series)
    move_hit, move_pct = signals.detect_move_full(series)
    year_high_hit, year_high_pct, prior_high = signals.detect_year_high_breakout_full(series)

    today = today_str()
    price = float(series["close"][-1])
    events_today = []

    new_state["last_close"] = price
    if vol_avg20 is not None:
        new_state["volume_avg20"] = vol_avg20
    if prior_high is not None:
        new_state["year_high_252"] = prior_high

    if cross:
        key = f"{cross}_last_alert_date"
        if entry_state.get(key) != today:
            alerts.append(fmt_cross(ticker, cross))
            new_state[key] = today
            events_today.append({"type": cross, "value": None})

    if vol_breakout and entry_state.get("volume_breakout_last_alert_date") != today:
        alerts.append(fmt_volume(ticker, vol_ratio))
        new_state["volume_breakout_last_alert_date"] = today
        events_today.append({"type": "volume_breakout", "value": f"{vol_ratio:.1f}x"})

    if move_hit and entry_state.get("move_alert_last_alert_date") != today:
        direction_sign = series["close"][-1] - series["close"][-2]
        alerts.append(fmt_move(ticker, move_pct, direction_sign))
        new_state["move_alert_last_alert_date"] = today
        sign = "+" if direction_sign >= 0 else "-"
        events_today.append({"type": "move", "value": f"{sign}{move_pct * 100:.1f}%"})

    if year_high_hit and entry_state.get("year_high_last_alert_date") != today:
        alerts.append(fmt_year_high(ticker, year_high_pct))
        new_state["year_high_last_alert_date"] = today
        value = f"+{year_high_pct:.1f}%" if year_high_pct is not None else None
        events_today.append({"type": "year_high", "value": value})

    result = {
        "data_status": "ok",
        "price": price,
        "trendstruktur": trendstruktur,
        "relative_staerke": relative_staerke,
        "fundamental": fundamental,
        "events_today": events_today,
    }
    return result, new_state


def run_intraday(ticker, entry_state, td_client, alerts):
    quote = td_client.get_quote(ticker)
    new_state = dict(entry_state)
    if quote is None:
        return {"data_status": "no_data"}, new_state

    today = today_str()
    events_today = []

    move_hit, move_pct = signals.detect_move_intraday(quote)
    if move_hit and entry_state.get("move_alert_last_alert_date") != today:
        direction_sign = quote["close"] - quote["previous_close"]
        alerts.append(fmt_move(ticker, move_pct, direction_sign))
        new_state["move_alert_last_alert_date"] = today
        sign = "+" if direction_sign >= 0 else "-"
        events_today.append({"type": "move", "value": f"{sign}{move_pct * 100:.1f}%"})

    cached_avg20 = entry_state.get("volume_avg20")
    if cached_avg20:
        vol_breakout, vol_ratio = signals.detect_volume_breakout_intraday(quote["volume"], cached_avg20)
        if vol_breakout and entry_state.get("volume_breakout_last_alert_date") != today:
            alerts.append(fmt_volume(ticker, vol_ratio))
            new_state["volume_breakout_last_alert_date"] = today
            events_today.append({"type": "volume_breakout", "value": f"{vol_ratio:.1f}x"})

    cached_year_high = entry_state.get("year_high_252")
    if cached_year_high:
        year_high_hit, year_high_pct = signals.detect_year_high_breakout_intraday(quote["close"], cached_year_high)
        if year_high_hit and entry_state.get("year_high_last_alert_date") != today:
            alerts.append(fmt_year_high(ticker, year_high_pct))
            new_state["year_high_last_alert_date"] = today
            value = f"+{year_high_pct:.1f}%" if year_high_pct is not None else None
            events_today.append({"type": "year_high", "value": value})

    result = {
        "data_status": "ok",
        "price": quote["close"],
        "trendstruktur": {"status": entry_state.get("trendstruktur_status", "grey")},
        "relative_staerke": {"status": entry_state.get("relative_staerke_status", "grey")},
        "fundamental": {"status": entry_state.get("fundamental_status", "grey")},
        "events_today": events_today,
    }
    return result, new_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "intraday"], required=True)
    args = parser.parse_args()

    td_key = os.environ.get("TWELVEDATA_API_KEY")
    if not td_key:
        print("TWELVEDATA_API_KEY missing", file=sys.stderr)
        sys.exit(1)
    td_client = TwelveDataClient(td_key)

    fh_key = os.environ.get("FINNHUB_API_KEY")
    fh_client = FinnhubClient(fh_key) if fh_key else None
    if not fh_client:
        print("[warn] FINNHUB_API_KEY missing: News und Fundamental-Ampel werden uebersprungen", file=sys.stderr)

    watchlist = load_watchlist(config.WATCHLIST_CSV)
    state = load_state(config.STATE_JSON)
    alerts = []
    results = {}

    spy_series = None
    if args.mode == "full":
        try:
            spy_series = td_client.get_time_series(config.BENCHMARK_SYMBOL)
        except ProviderError as e:
            print(f"[error] Could not fetch benchmark {config.BENCHMARK_SYMBOL}: {e}", file=sys.stderr)
            sys.exit(1)
        if spy_series is None:
            print(f"[error] Benchmark {config.BENCHMARK_SYMBOL} returned no data", file=sys.stderr)
            sys.exit(1)

    new_state_all = {}
    for row in watchlist:
        ticker = row["ticker"].strip()
        entry_state = state.get(ticker, {})
        try:
            if args.mode == "full":
                result, new_state = run_full(ticker, entry_state, td_client, fh_client, spy_series, alerts)
            else:
                result, new_state = run_intraday(ticker, entry_state, td_client, alerts)
        except ProviderError as e:
            print(f"[warn] {ticker}: {e}", file=sys.stderr)
            result, new_state = {"data_status": "error", "error": str(e)}, entry_state

        process_news(ticker, entry_state, new_state, fh_client, alerts)
        result["note"] = row.get("note", "")
        results[ticker] = result
        new_state_all[ticker] = new_state

    save_state(config.STATE_JSON, new_state_all)
    save_results(config.RESULTS_JSON, results, args.mode, datetime.datetime.now(datetime.timezone.utc).isoformat())

    if alerts:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        message = "\n".join(alerts)
        print(message)
        if bot_token and chat_id:
            try:
                telegram.send_message(bot_token, chat_id, message)
            except telegram.TelegramError as e:
                print(f"[error] Telegram send failed: {e}", file=sys.stderr)
        else:
            print("[warn] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing, alerts not sent", file=sys.stderr)
    else:
        print("No new alerts this run.")


if __name__ == "__main__":
    main()
