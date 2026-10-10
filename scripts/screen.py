"""Main entry point. Run modes:

  python screen.py --mode full      -> alle Kriterien, einmal taeglich nach US-Handelsschluss
  python screen.py --mode intraday  -> nur Volumen-Breakout, Move/Gap, 52-Wochen-Hoch, News

Reads the watchlist (Google Sheet via WATCHLIST_CSV_URL, Fallback data/watchlist.csv, see
watchlist_source.py) + data/state.json, writes data/state.json + docs/results.json (und
data/watchlist.csv mit dem normalisierten Sheet-Stand). Ticker ohne vollen Lauf im State
werden auch im Intraday-Modus voll analysiert.

Telegram: die drei Ampel-Status-Wechsel (Trendstruktur/Relative Staerke/Fundamental) werden
weiterhin in EINER gebuendelten Nachricht verschickt (unveraendert seit v2). Jedes ausgeloeste
Einzelereignis-Signal (Cross, Volumen-Breakout, Move, 52W-Hoch, Pocket Pivot, Aroon-Crossover,
TTM-Squeeze-Fire) wird dagegen als EIGENE Telegram-Nachricht verschickt, nicht gebuendelt (v3).
"""

import argparse
import datetime
import html
import json
import os
import sys
import urllib.parse

import config
import signals
import telegram
import watchlist_source
from providers import FinnhubClient, ProviderError, TwelveDataClient


def needs_full_analysis(entry_state):
    """Ticker ohne abgeschlossenen vollen Lauf (kein last_close im State) oder wieder neu in
    die Watchlist aufgenommene Ticker bekommen die volle Analyse auch in einem Intraday-Lauf."""
    return bool(entry_state.get("inactive")) or "last_close" not in entry_state


def carry_over_inactive_state(state, active_tickers):
    """State-Eintraege von Tickern, die nicht mehr in der Watchlist stehen, bleiben erhalten
    (Dedup-Historie), werden aber als inactive markiert. Kommt der Ticker spaeter zurueck,
    laeuft er dadurch wieder durch die volle Analyse statt mit veralteten Cache-Werten."""
    return {
        ticker: {**entry, "inactive": True}
        for ticker, entry in state.items()
        if ticker not in active_tickers
    }


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


def save_results(path, results, mode, run_timestamp, watchlist_edit_url=""):
    payload = {
        "last_run_at": run_timestamp,
        "last_run_mode": mode,
        "watchlist_edit_url": watchlist_edit_url,
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


def ticker_link(ticker):
    """Ticker als Telegram-HTML-Link auf die Chart-Seite (config.CHART_URL_TEMPLATE).
    Yahoo schreibt Klassen-Ticker mit Bindestrich (BRK.B -> BRK-B)."""
    symbol = urllib.parse.quote(ticker.replace(".", "-"), safe="")
    url = config.CHART_URL_TEMPLATE.format(ticker=symbol)
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(ticker)}</a>'


def fmt_status_change(ticker, label, status):
    icon = "\U0001F7E2" if status == "green" else "\U0001F534"
    verb = "GRUEN" if status == "green" else "ROT"
    return f"{icon} {ticker_link(ticker)}: {html.escape(label)} {verb}"


def fmt_news(ticker, item):
    headline = html.escape(item.get("headline", "").strip())
    url = html.escape(item.get("url", ""))
    return f"\U0001F4F0 {ticker_link(ticker)}: {headline} {url}".strip()


def fmt_pct_signed(value_fraction):
    """value_fraction: signed fraction, e.g. 0.068 -> '+6.8%'."""
    sign = "+" if value_fraction >= 0 else ""
    return f"{sign}{value_fraction * 100:.1f}%"


def fmt_signal(ticker, label, direction, rvol_ratio, extra=None):
    """Builds one Telegram-ready line for a single event-signal, per the v3 format:
    {Ampel-Emoji} {TICKER} | {Signalname} | RVOL {Wert}x ({Tier}) | {Zusatzwert}
    direction: 'bullish' or 'bearish'. Bearish is always red, regardless of RVOL-Tier."""
    tier = signals.rvol_tier(rvol_ratio)
    if direction == "bearish":
        emoji = "\U0001F534"
    else:
        emoji = "\U0001F7E2" if tier in ("High", "Extreme") else "\U0001F7E1"
    rvol_text = f"RVOL {rvol_ratio:.1f}x ({tier})" if rvol_ratio is not None else "RVOL n/a"
    line = f"{emoji} {ticker_link(ticker)} | {html.escape(label)} | {rvol_text}"
    if extra:
        line += f" | {html.escape(extra)}"
    return line


def event_entry(type_, label, direction, value, rvol_ratio):
    return {
        "type": type_,
        "label": label,
        "direction": direction,
        "value": value,
        "rvol_ratio": rvol_ratio,
        "rvol_tier": signals.rvol_tier(rvol_ratio),
    }


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


def apply_status_alerts(ticker, entry_state, new_state, results_out, status_alerts):
    """Compares each of the three independent ampeln to its previous state and appends
    a status-change alert (only) when it actually flipped between green and red."""
    for status_key, status_result in results_out.items():
        label = STATUS_LABELS[status_key]
        prev_status = entry_state.get(status_key)
        if status_result["status"] in ("green", "red"):
            new_state[status_key] = status_result["status"]
            if prev_status in ("green", "red") and prev_status != status_result["status"]:
                status_alerts.append(fmt_status_change(ticker, label, status_result["status"]))


def run_full(ticker, entry_state, td_client, fh_client, spy_series, status_alerts, event_alerts):
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
        status_alerts,
    )

    today = today_str()
    price = float(series["close"][-1])
    events_today = []

    vol_avg20 = signals.compute_volume_avg20(series)
    rvol_ratio = signals.compute_rvol(float(series["volume"][-1]), vol_avg20)
    move_signed_pct = (
        (series["close"][-1] - series["close"][-2]) / series["close"][-2]
        if len(series["close"]) >= 2 and series["close"][-2] != 0
        else None
    )

    new_state["last_close"] = price
    if vol_avg20 is not None:
        new_state["volume_avg20"] = vol_avg20

    cross = signals.detect_cross(series)
    if cross:
        key = f"{cross}_last_alert_date"
        if entry_state.get(key) != today:
            direction = "bullish" if cross == "golden_cross" else "bearish"
            label = "Golden Cross 50/200" if cross == "golden_cross" else "Death Cross 50/200"
            event_alerts.append(fmt_signal(ticker, label, direction, rvol_ratio))
            new_state[key] = today
            events_today.append(event_entry(cross, label, direction, None, rvol_ratio))

    vol_breakout, vol_ratio, _ = signals.detect_volume_breakout_full(series)
    if vol_breakout and entry_state.get("volume_breakout_last_alert_date") != today:
        extra = fmt_pct_signed(move_signed_pct) if move_signed_pct is not None else None
        event_alerts.append(fmt_signal(ticker, "Volumen-Breakout", "bullish", rvol_ratio, extra))
        new_state["volume_breakout_last_alert_date"] = today
        events_today.append(event_entry("volume_breakout", "Volumen-Breakout", "bullish", extra, rvol_ratio))

    move_hit, move_pct = signals.detect_move_full(series)
    if move_hit and entry_state.get("move_alert_last_alert_date") != today:
        direction = "bullish" if move_signed_pct >= 0 else "bearish"
        value = fmt_pct_signed(move_signed_pct)
        event_alerts.append(fmt_signal(ticker, "Kursbewegung", direction, rvol_ratio, value))
        new_state["move_alert_last_alert_date"] = today
        events_today.append(event_entry("move", "Kursbewegung", direction, value, rvol_ratio))

    year_high_hit, year_high_pct, prior_high = signals.detect_year_high_breakout_full(series)
    if prior_high is not None:
        new_state["year_high_252"] = prior_high
    if year_high_hit and entry_state.get("year_high_last_alert_date") != today:
        value = f"+{year_high_pct:.1f}%" if year_high_pct is not None else None
        event_alerts.append(fmt_signal(ticker, "52-Wochen-Hoch", "bullish", rvol_ratio, value))
        new_state["year_high_last_alert_date"] = today
        events_today.append(event_entry("year_high", "52-Wochen-Hoch", "bullish", value, rvol_ratio))

    if signals.detect_pocket_pivot(series) and entry_state.get("pocket_pivot_last_alert_date") != today:
        event_alerts.append(fmt_signal(ticker, "Pocket Pivot", "bullish", rvol_ratio))
        new_state["pocket_pivot_last_alert_date"] = today
        events_today.append(event_entry("pocket_pivot", "Pocket Pivot", "bullish", None, rvol_ratio))

    aroon_cross = signals.detect_aroon_crossover(series)
    if aroon_cross:
        key = f"aroon_{'bull' if aroon_cross == 'bullish' else 'bear'}_last_alert_date"
        if entry_state.get(key) != today:
            label = f"Aroon-Crossover ({'bullisch' if aroon_cross == 'bullish' else 'bearisch'})"
            event_alerts.append(fmt_signal(ticker, label, aroon_cross, rvol_ratio))
            new_state[key] = today
            events_today.append(event_entry(f"aroon_{aroon_cross}", label, aroon_cross, None, rvol_ratio))

    squeeze_fire = signals.detect_ttm_squeeze_fire(series)
    if squeeze_fire:
        key = f"squeeze_{'bull' if squeeze_fire == 'bullish' else 'bear'}_last_alert_date"
        if entry_state.get(key) != today:
            label = f"TTM Squeeze Fire ({'bullisch' if squeeze_fire == 'bullish' else 'bearisch'})"
            event_alerts.append(fmt_signal(ticker, label, squeeze_fire, rvol_ratio))
            new_state[key] = today
            events_today.append(event_entry(f"squeeze_{squeeze_fire}", label, squeeze_fire, None, rvol_ratio))

    nr_flags = signals.compute_narrow_range_flags(series)
    new_state["nr4_active"] = nr_flags["nr4"]
    new_state["nr7_active"] = nr_flags["nr7"]

    result = {
        "data_status": "ok",
        "price": price,
        "trendstruktur": trendstruktur,
        "relative_staerke": relative_staerke,
        "fundamental": fundamental,
        "nr4_active": nr_flags["nr4"],
        "nr7_active": nr_flags["nr7"],
        "events_today": events_today,
    }
    return result, new_state


def run_intraday(ticker, entry_state, td_client, event_alerts):
    quote = td_client.get_quote(ticker)
    new_state = dict(entry_state)
    if quote is None:
        return {"data_status": "no_data"}, new_state

    today = today_str()
    events_today = []

    cached_avg20 = entry_state.get("volume_avg20")
    rvol_ratio = signals.compute_rvol(quote["volume"], cached_avg20)
    move_signed_pct = (
        (quote["close"] - quote["previous_close"]) / quote["previous_close"]
        if quote["previous_close"] else None
    )

    move_hit, move_pct = signals.detect_move_intraday(quote)
    if move_hit and entry_state.get("move_alert_last_alert_date") != today:
        direction = "bullish" if move_signed_pct >= 0 else "bearish"
        value = fmt_pct_signed(move_signed_pct)
        event_alerts.append(fmt_signal(ticker, "Kursbewegung", direction, rvol_ratio, value))
        new_state["move_alert_last_alert_date"] = today
        events_today.append(event_entry("move", "Kursbewegung", direction, value, rvol_ratio))

    if cached_avg20:
        vol_breakout, vol_ratio = signals.detect_volume_breakout_intraday(quote["volume"], cached_avg20)
        if vol_breakout and entry_state.get("volume_breakout_last_alert_date") != today:
            extra = fmt_pct_signed(move_signed_pct) if move_signed_pct is not None else None
            event_alerts.append(fmt_signal(ticker, "Volumen-Breakout", "bullish", rvol_ratio, extra))
            new_state["volume_breakout_last_alert_date"] = today
            events_today.append(event_entry("volume_breakout", "Volumen-Breakout", "bullish", extra, rvol_ratio))

    cached_year_high = entry_state.get("year_high_252")
    if cached_year_high:
        year_high_hit, year_high_pct = signals.detect_year_high_breakout_intraday(quote["close"], cached_year_high)
        if year_high_hit and entry_state.get("year_high_last_alert_date") != today:
            value = f"+{year_high_pct:.1f}%" if year_high_pct is not None else None
            event_alerts.append(fmt_signal(ticker, "52-Wochen-Hoch", "bullish", rvol_ratio, value))
            new_state["year_high_last_alert_date"] = today
            events_today.append(event_entry("year_high", "52-Wochen-Hoch", "bullish", value, rvol_ratio))

    result = {
        "data_status": "ok",
        "price": quote["close"],
        "trendstruktur": {"status": entry_state.get("trendstruktur_status", "grey")},
        "relative_staerke": {"status": entry_state.get("relative_staerke_status", "grey")},
        "fundamental": {"status": entry_state.get("fundamental_status", "grey")},
        "nr4_active": entry_state.get("nr4_active", False),
        "nr7_active": entry_state.get("nr7_active", False),
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

    try:
        watchlist, watchlist_origin = watchlist_source.load_watchlist(
            os.environ.get("WATCHLIST_CSV_URL", ""),
            config.WATCHLIST_CSV,
            timeout=config.WATCHLIST_CSV_TIMEOUT_S,
            retries=config.WATCHLIST_CSV_RETRIES,
        )
    except watchlist_source.WatchlistSourceError as e:
        print(f"[error] Keine brauchbare Watchlist: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[info] Watchlist: {len(watchlist)} Ticker aus {'Google Sheet' if watchlist_origin == 'sheet' else config.WATCHLIST_CSV}")

    state = load_state(config.STATE_JSON)
    status_alerts = []  # Ampel-Statuswechsel: eine gebuendelte Nachricht
    event_alerts = []    # Einzelereignis-Signale: eine Nachricht PRO Signal (v3)
    news_alerts = []
    results = {}

    active_tickers = {row["ticker"] for row in watchlist}
    # Neue (bzw. wieder aufgenommene) Ticker bekommen auch im Intraday-Lauf die volle Analyse.
    full_candidates = {
        row["ticker"] for row in watchlist if needs_full_analysis(state.get(row["ticker"], {}))
    }

    spy_series = None
    if args.mode == "full" or full_candidates:
        try:
            spy_series = td_client.get_time_series(config.BENCHMARK_SYMBOL)
        except ProviderError as e:
            if args.mode == "full":
                print(f"[error] Could not fetch benchmark {config.BENCHMARK_SYMBOL}: {e}", file=sys.stderr)
                sys.exit(1)
            print(f"[warn] Benchmark {config.BENCHMARK_SYMBOL} nicht ladbar ({e}), neue Ticker "
                  "laufen in diesem Intraday-Lauf nur im Intraday-Umfang", file=sys.stderr)
        if spy_series is None and args.mode == "full":
            print(f"[error] Benchmark {config.BENCHMARK_SYMBOL} returned no data", file=sys.stderr)
            sys.exit(1)

    # Entfernte Ticker fallen aus results.json heraus, ihr State-Eintrag bleibt (Dedup-Historie).
    new_state_all = carry_over_inactive_state(state, active_tickers)
    for row in watchlist:
        ticker = row["ticker"]
        entry_state = state.get(ticker, {})
        use_full = args.mode == "full" or (spy_series is not None and ticker in full_candidates)
        if use_full and args.mode != "full":
            print(f"[info] {ticker}: neu in der Watchlist, volle Analyse im Intraday-Lauf")
        try:
            if use_full:
                result, new_state = run_full(
                    ticker, entry_state, td_client, fh_client, spy_series, status_alerts, event_alerts
                )
            else:
                result, new_state = run_intraday(ticker, entry_state, td_client, event_alerts)
        except ProviderError as e:
            print(f"[warn] {ticker}: {e}", file=sys.stderr)
            result, new_state = {"data_status": "error", "error": str(e)}, dict(entry_state)

        new_state.pop("inactive", None)
        process_news(ticker, entry_state, new_state, fh_client, news_alerts)
        result["note"] = row.get("note", "")
        result["company_name"] = row.get("company_name", "")
        results[ticker] = result
        new_state_all[ticker] = new_state

    save_state(config.STATE_JSON, new_state_all)
    save_results(
        config.RESULTS_JSON,
        results,
        args.mode,
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        config.WATCHLIST_SHEET_EDIT_URL,
    )

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    # Ampel-Statuswechsel + News bleiben wie vor v3 in einer gebuendelten Nachricht
    # (von diesem Nachtrag nicht betroffen). Einzelereignis-Signale sind neu je eine
    # eigene Nachricht (siehe fmt_signal/event_alerts).
    bundled = status_alerts + news_alerts

    if not (bot_token and chat_id):
        if bundled or event_alerts:
            print("[warn] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing, alerts not sent", file=sys.stderr)
        for line in bundled + event_alerts:
            print(line)
        if not (bundled or event_alerts):
            print("No new alerts this run.")
        return

    if bundled:
        try:
            telegram.send_message(bot_token, chat_id, "\n".join(bundled))
        except telegram.TelegramError as e:
            print(f"[error] Telegram send failed (status/news): {e}", file=sys.stderr)

    # v3: ein Einzelereignis-Signal = eine eigene Telegram-Nachricht, nicht gebuendelt.
    for line in event_alerts:
        try:
            telegram.send_message(bot_token, chat_id, line)
        except telegram.TelegramError as e:
            print(f"[error] Telegram send failed ({line}): {e}", file=sys.stderr)

    if not (bundled or event_alerts):
        print("No new alerts this run.")


if __name__ == "__main__":
    main()
