"""Main entry point. Run modes:

  python screen.py --mode full      -> alle Kriterien (1-6), einmal taeglich nach US-Handelsschluss
  python screen.py --mode intraday  -> nur Kriterien 4,5,6, viermal taeglich

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


def load_state(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


def fmt_trend_change(ticker, status):
    icon = "\U0001F7E2" if status == "green" else "\U0001F534"
    label = "Trend-Template GRUEN (Kriterien erfuellt)" if status == "green" else "Trend-Template ROT (Kriterien nicht mehr erfuellt)"
    return f"{icon} {ticker}: {label}"


def fmt_cross(ticker, cross_type):
    if cross_type == "golden_cross":
        return f"\U0001F7E2 {ticker}: Golden Cross (SMA50 > SMA200)"
    return f"\U0001F534 {ticker}: Death Cross (SMA50 < SMA200)"


def fmt_volume(ticker, ratio):
    return f"\U0001F7E1 {ticker}: Volumen-Breakout ({ratio:.1f}x 20-Tage-Durchschnitt)"


def fmt_move(ticker, move_pct, direction_sign):
    sign = "+" if direction_sign >= 0 else "-"
    return f"\U0001F7E1 {ticker}: Kursbewegung {sign}{move_pct * 100:.1f}% ggue. letztem Schlusskurs"


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


def run_full(ticker, entry_state, td_client, spy_series, alerts):
    """Returns (result_dict, new_state_dict)."""
    series = td_client.get_time_series(ticker)
    new_state = dict(entry_state)
    if series is None or len(series["close"]) == 0:
        return {"data_status": "no_data"}, new_state

    trend = signals.evaluate_trend_template(series, spy_series)
    cross = signals.detect_cross(series)
    vol_breakout, vol_ratio, vol_avg20 = signals.detect_volume_breakout_full(series)
    move_hit, move_pct = signals.detect_move_full(series)
    today = today_str()
    price = float(series["close"][-1])

    new_state["last_close"] = price
    if vol_avg20 is not None:
        new_state["volume_avg20"] = vol_avg20

    prev_status = entry_state.get("trend_template_status")
    if trend["status"] in ("green", "red"):
        new_state["trend_template_status"] = trend["status"]
        if prev_status in ("green", "red") and prev_status != trend["status"]:
            alerts.append(fmt_trend_change(ticker, trend["status"]))

    if cross:
        key = f"{cross}_last_alert_date"
        if entry_state.get(key) != today:
            alerts.append(fmt_cross(ticker, cross))
            new_state[key] = today

    if vol_breakout and entry_state.get("volume_breakout_last_alert_date") != today:
        alerts.append(fmt_volume(ticker, vol_ratio))
        new_state["volume_breakout_last_alert_date"] = today

    if move_hit and entry_state.get("move_alert_last_alert_date") != today:
        direction_sign = series["close"][-1] - series["close"][-2]
        alerts.append(fmt_move(ticker, move_pct, direction_sign))
        new_state["move_alert_last_alert_date"] = today

    result = {
        "data_status": "ok",
        "price": price,
        "trend_template_status": trend["status"],
        "trend_template_reason": trend.get("reason"),
        "sma50": trend.get("sma50"),
        "sma150": trend.get("sma150"),
        "sma200": trend.get("sma200"),
        "rs": trend.get("rs"),
        "rs_trend": trend.get("rs_trend"),
        "events_today": {
            "golden_cross": cross == "golden_cross",
            "death_cross": cross == "death_cross",
            "volume_breakout": bool(vol_breakout),
            "move": bool(move_hit),
        },
    }
    return result, new_state


def run_intraday(ticker, entry_state, td_client, alerts):
    quote = td_client.get_quote(ticker)
    new_state = dict(entry_state)
    if quote is None:
        return {"data_status": "no_data"}, new_state

    today = today_str()
    move_hit, move_pct = signals.detect_move_intraday(quote)
    if move_hit and entry_state.get("move_alert_last_alert_date") != today:
        direction_sign = quote["close"] - quote["previous_close"]
        alerts.append(fmt_move(ticker, move_pct, direction_sign))
        new_state["move_alert_last_alert_date"] = today

    vol_breakout = False
    vol_ratio = None
    cached_avg20 = entry_state.get("volume_avg20")
    if cached_avg20:
        vol_breakout, vol_ratio = signals.detect_volume_breakout_intraday(quote["volume"], cached_avg20)
        if vol_breakout and entry_state.get("volume_breakout_last_alert_date") != today:
            alerts.append(fmt_volume(ticker, vol_ratio))
            new_state["volume_breakout_last_alert_date"] = today

    result = {
        "data_status": "ok",
        "price": quote["close"],
        "trend_template_status": entry_state.get("trend_template_status", "grey"),
        "events_today": {
            "golden_cross": False,
            "death_cross": False,
            "volume_breakout": bool(vol_breakout),
            "move": bool(move_hit),
        },
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

    fh_client = None
    if config.ENABLE_NEWS:
        fh_key = os.environ.get("FINNHUB_API_KEY")
        if fh_key:
            fh_client = FinnhubClient(fh_key)
        else:
            print("[warn] ENABLE_NEWS=True but FINNHUB_API_KEY missing, skipping news", file=sys.stderr)

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
                result, new_state = run_full(ticker, entry_state, td_client, spy_series, alerts)
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
