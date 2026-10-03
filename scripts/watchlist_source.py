"""Watchlist laden: Google Sheet (als CSV im Web veroeffentlicht), Fallback auf data/watchlist.csv.

Ablauf in load_watchlist():
  1. csv_url gesetzt -> CSV per HTTP holen (Timeout, Retries), pruefen, normalisieren.
     Bei Erfolg wird der normalisierte Stand nach fallback_path zurueckgeschrieben.
  2. URL leer, Ladefehler oder unbrauchbarer Inhalt -> Warnung + Fallback auf fallback_path.
  3. Auch der Fallback unbrauchbar -> WatchlistSourceError. Es wird nie mit einer leeren
     Watchlist weitergelaufen.

Die Sheet-URL wird nie ins Log geschrieben (nur Fehlerklasse bzw. HTTP-Status).
"""

import csv
import datetime
import io
import os
import re
import sys
import time

import requests

TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")
FIELDNAMES = ["ticker", "since", "note", "company_name"]
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y")


class WatchlistSourceError(Exception):
    pass


def _warn(message):
    print(f"[warn] {message}", file=sys.stderr)


def parse_since(value):
    """ISO (2026-10-03) und deutsches Format (03.10.2026 bzw. 3.10.2026) -> ISO-String.
    Leer -> "". Nicht lesbar -> None."""
    value = (value or "").strip()
    if not value:
        return ""
    date_part = value.split(" ")[0].split("T")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(date_part, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_watchlist_csv(text):
    """Prueft und normalisiert CSV-Text. Gibt Liste von Dicts mit FIELDNAMES zurueck.
    Raises WatchlistSourceError bei fehlender Kopfzeile 'ticker' oder wenn kein gueltiger
    Ticker uebrig bleibt."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise WatchlistSourceError("CSV ist leer")
    columns = {name.strip().lower(): name for name in reader.fieldnames if name}
    if "ticker" not in columns:
        raise WatchlistSourceError("Kopfzeile enthaelt keine Spalte 'ticker'")

    def cell(raw, key):
        column = columns.get(key)
        return (raw.get(column) or "").strip() if column else ""

    rows, seen = [], set()
    for raw in reader:
        ticker = cell(raw, "ticker").upper()
        if not ticker:
            continue
        if not TICKER_RE.match(ticker):
            _warn(f"Zeile {reader.line_num}: ungueltiger Ticker {ticker!r}, uebersprungen")
            continue
        if ticker in seen:
            _warn(f"Zeile {reader.line_num}: Duplikat {ticker}, uebersprungen")
            continue
        seen.add(ticker)

        since = parse_since(cell(raw, "since"))
        if since is None:
            _warn(f"{ticker}: Datum {cell(raw, 'since')!r} nicht lesbar, 'since' bleibt leer")
            since = ""
        rows.append({
            "ticker": ticker,
            "since": since,
            "note": cell(raw, "note"),
            "company_name": cell(raw, "company_name"),
        })

    if not rows:
        raise WatchlistSourceError("keine gueltigen Ticker in der CSV")
    return rows


def fetch_csv_text(url, timeout=15, retries=2, sleep=time.sleep):
    """Holt die CSV. Insgesamt retries + 1 Versuche mit kurzem Backoff."""
    failure = "unbekannt"
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                try:
                    return resp.content.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise WatchlistSourceError("Antwort ist kein UTF-8")
            failure = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            failure = type(e).__name__
        if attempt < retries:
            sleep(2 ** attempt)
    raise WatchlistSourceError(f"{failure} nach {retries + 1} Versuchen")


def write_watchlist_csv(path, rows):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, path)


def load_fallback(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise WatchlistSourceError(f"Fallback {path} nicht lesbar ({type(e).__name__})")
    try:
        return parse_watchlist_csv(text)
    except WatchlistSourceError as e:
        raise WatchlistSourceError(f"Fallback {path} unbrauchbar ({e})")


def load_watchlist(csv_url, fallback_path, timeout=15, retries=2, sleep=time.sleep):
    """Returns (rows, source) mit source "sheet" oder "fallback".
    Raises WatchlistSourceError, wenn keine der beiden Quellen brauchbar ist."""
    csv_url = (csv_url or "").strip()
    if csv_url:
        try:
            rows = parse_watchlist_csv(fetch_csv_text(csv_url, timeout, retries, sleep))
        except WatchlistSourceError as e:
            _warn(f"Watchlist aus Google Sheet nicht nutzbar ({e}), Fallback auf {fallback_path}")
        else:
            try:
                write_watchlist_csv(fallback_path, rows)
            except OSError as e:
                _warn(f"{fallback_path} konnte nicht aktualisiert werden ({type(e).__name__})")
            return rows, "sheet"
    else:
        _warn(f"WATCHLIST_CSV_URL nicht gesetzt, nutze {fallback_path}")

    return load_fallback(fallback_path), "fallback"
