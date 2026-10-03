import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import watchlist_source as ws  # noqa: E402

SECRET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-SECRET/pub?output=csv"

FALLBACK_CSV = "ticker,since,note,company_name\nOLD1,2026-01-01,,Old One Inc\nOLD2,2026-01-02,,Old Two Inc\n"


def response(status=200, text=""):
    resp = mock.Mock()
    resp.status_code = status
    resp.content = text.encode("utf-8")
    return resp


class ParseWatchlistCsvTests(unittest.TestCase):
    def test_valid_csv_is_normalized(self):
        text = (
            "ticker,since,note,company_name\n"
            " twst ,2026-09-20,,Twist Bioscience Corp\n"
            "QBTS,2026-09-21,watch me,D-Wave Quantum Inc\n"
        )
        rows = ws.parse_watchlist_csv(text)
        self.assertEqual(
            rows,
            [
                {"ticker": "TWST", "since": "2026-09-20", "note": "", "company_name": "Twist Bioscience Corp"},
                {"ticker": "QBTS", "since": "2026-09-21", "note": "watch me", "company_name": "D-Wave Quantum Inc"},
            ],
        )

    def test_missing_ticker_column_raises(self):
        with self.assertRaises(ws.WatchlistSourceError):
            ws.parse_watchlist_csv("symbol,since\nTWST,2026-09-20\n")

    def test_html_instead_of_csv_raises(self):
        with self.assertRaises(ws.WatchlistSourceError):
            ws.parse_watchlist_csv("<!DOCTYPE html><html><body>Sign in</body></html>")

    def test_header_only_raises(self):
        with self.assertRaises(ws.WatchlistSourceError):
            ws.parse_watchlist_csv("ticker,since,note,company_name\n")

    def test_missing_optional_columns_are_filled(self):
        rows = ws.parse_watchlist_csv("ticker\nTWST\n")
        self.assertEqual(rows, [{"ticker": "TWST", "since": "", "note": "", "company_name": ""}])

    def test_bom_and_header_case_are_tolerated(self):
        rows = ws.parse_watchlist_csv("﻿Ticker,Since\nTWST,2026-09-20\n")
        self.assertEqual(rows[0]["ticker"], "TWST")
        self.assertEqual(rows[0]["since"], "2026-09-20")

    def test_duplicates_blank_rows_and_invalid_tickers_are_dropped(self):
        text = (
            "ticker,since,note,company_name\n"
            "TWST,2026-09-20,first,One\n"
            ",,,\n"
            "twst,2026-09-21,second,Two\n"
            "BAD TICKER,2026-09-20,,\n"
            "WAYTOOLONGTICKER,2026-09-20,,\n"
            "BRK.B,2026-09-20,,\n"
        )
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rows = ws.parse_watchlist_csv(text)
        self.assertEqual([r["ticker"] for r in rows], ["TWST", "BRK.B"])
        self.assertEqual(rows[0]["note"], "first")
        self.assertIn("Duplikat TWST", err.getvalue())
        self.assertIn("ungueltiger Ticker", err.getvalue())

    def test_german_date_format(self):
        text = "ticker,since\nA,03.10.2026\nB,3.10.2026\nC,03.10.26\nD,2026-10-03\nE,\n"
        rows = ws.parse_watchlist_csv(text)
        self.assertEqual([r["since"] for r in rows], ["2026-10-03"] * 4 + [""])

    def test_unparseable_date_keeps_ticker(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rows = ws.parse_watchlist_csv("ticker,since\nTWST,irgendwann\n")
        self.assertEqual(rows, [{"ticker": "TWST", "since": "", "note": "", "company_name": ""}])
        self.assertIn("nicht lesbar", err.getvalue())

    def test_note_with_comma_is_preserved(self):
        rows = ws.parse_watchlist_csv('ticker,note\nTWST,"a, b"\n')
        self.assertEqual(rows[0]["note"], "a, b")


class LoadWatchlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fallback_path = os.path.join(self.tmp.name, "watchlist.csv")
        with open(self.fallback_path, "w", encoding="utf-8", newline="") as f:
            f.write(FALLBACK_CSV)

    def load(self, url=SECRET_URL):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rows, source = ws.load_watchlist(url, self.fallback_path, sleep=lambda s: None)
        return rows, source, err.getvalue()

    def read_fallback_file(self):
        with open(self.fallback_path, encoding="utf-8", newline="") as f:
            return f.read()

    def test_success_uses_sheet_and_writes_back_normalized_csv(self):
        sheet = "ticker,since,note,company_name\n twst ,03.10.2026,,Twist Bioscience Corp\nTWST,2026-10-04,,dup\n"
        with mock.patch.object(ws.requests, "get", return_value=response(200, sheet)):
            rows, source, _ = self.load()
        self.assertEqual(source, "sheet")
        self.assertEqual([r["ticker"] for r in rows], ["TWST"])
        self.assertEqual(
            self.read_fallback_file(),
            "ticker,since,note,company_name\nTWST,2026-10-03,,Twist Bioscience Corp\n",
        )

    def test_http_error_triggers_fallback_after_retries(self):
        with mock.patch.object(ws.requests, "get", return_value=response(500)) as get:
            rows, source, stderr = self.load()
        self.assertEqual(get.call_count, 3)  # 1 Versuch + 2 Retries
        self.assertEqual(source, "fallback")
        self.assertEqual([r["ticker"] for r in rows], ["OLD1", "OLD2"])
        self.assertIn("[warn]", stderr)
        self.assertIn("HTTP 500", stderr)
        self.assertEqual(self.read_fallback_file(), FALLBACK_CSV)

    def test_network_error_triggers_fallback_and_does_not_leak_url(self):
        error = requests.ConnectionError(f"Max retries exceeded with url: {SECRET_URL}")
        with mock.patch.object(ws.requests, "get", side_effect=error):
            rows, source, stderr = self.load()
        self.assertEqual(source, "fallback")
        self.assertEqual([r["ticker"] for r in rows], ["OLD1", "OLD2"])
        self.assertIn("ConnectionError", stderr)
        self.assertNotIn("SECRET", stderr)

    def test_timeout_uses_configured_timeout_and_falls_back(self):
        with mock.patch.object(ws.requests, "get", side_effect=requests.Timeout()) as get:
            with contextlib.redirect_stderr(io.StringIO()):
                rows, source = ws.load_watchlist(
                    SECRET_URL, self.fallback_path, timeout=15, retries=2, sleep=lambda s: None
                )
        self.assertEqual(source, "fallback")
        self.assertEqual(get.call_args.kwargs["timeout"], 15)

    def test_retry_then_success(self):
        sheet = "ticker\nTWST\n"
        with mock.patch.object(ws.requests, "get", side_effect=[response(503), response(200, sheet)]):
            rows, source, _ = self.load()
        self.assertEqual(source, "sheet")
        self.assertEqual([r["ticker"] for r in rows], ["TWST"])

    def test_empty_url_uses_fallback_with_warning(self):
        with mock.patch.object(ws.requests, "get") as get:
            rows, source, stderr = self.load(url="")
        get.assert_not_called()
        self.assertEqual(source, "fallback")
        self.assertIn("WATCHLIST_CSV_URL nicht gesetzt", stderr)

    def test_invalid_sheet_content_triggers_fallback(self):
        html = "<!DOCTYPE html><html><body>Anmelden</body></html>"
        with mock.patch.object(ws.requests, "get", return_value=response(200, html)):
            rows, source, stderr = self.load()
        self.assertEqual(source, "fallback")
        self.assertIn("[warn]", stderr)
        self.assertEqual(self.read_fallback_file(), FALLBACK_CSV)

    def test_sheet_without_valid_tickers_never_yields_empty_watchlist(self):
        with mock.patch.object(ws.requests, "get", return_value=response(200, "ticker,since\n,\n")):
            rows, source, _ = self.load()
        self.assertEqual(source, "fallback")
        self.assertEqual(len(rows), 2)

    def test_both_sources_unusable_raises(self):
        os.remove(self.fallback_path)
        with mock.patch.object(ws.requests, "get", return_value=response(500)):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(ws.WatchlistSourceError):
                    ws.load_watchlist(SECRET_URL, self.fallback_path, sleep=lambda s: None)

    def test_fallback_without_ticker_column_raises(self):
        with open(self.fallback_path, "w", encoding="utf-8") as f:
            f.write("foo,bar\n1,2\n")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ws.WatchlistSourceError):
                ws.load_watchlist("", self.fallback_path)


if __name__ == "__main__":
    unittest.main()
