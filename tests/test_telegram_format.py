import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import screen  # noqa: E402
import telegram  # noqa: E402


def response(status=200, text="{}"):
    resp = mock.Mock()
    resp.status_code = status
    resp.text = text
    return resp


class TickerLinkTests(unittest.TestCase):
    def test_links_to_yahoo_chart_page(self):
        self.assertEqual(
            screen.ticker_link("TWST"),
            '<a href="https://finance.yahoo.com/quote/TWST">TWST</a>',
        )

    def test_class_share_ticker_uses_yahoo_dash(self):
        link = screen.ticker_link("BRK.B")
        self.assertIn("/quote/BRK-B", link)
        self.assertIn(">BRK.B</a>", link)


class SignalLineTests(unittest.TestCase):
    def test_signal_line_keeps_format_and_links_ticker(self):
        line = screen.fmt_signal("QBTS", "Volumen-Breakout", "bullish", 3.2, "+6.8%")
        self.assertEqual(
            line,
            "\U0001F7E2 " + screen.ticker_link("QBTS") + " | Volumen-Breakout | RVOL 3.2x (Extreme) | +6.8%",
        )

    def test_bearish_signal_without_extra(self):
        line = screen.fmt_signal("FLY", "Death Cross 50/200", "bearish", 1.1)
        self.assertTrue(line.startswith("\U0001F534 " + screen.ticker_link("FLY")))
        self.assertTrue(line.endswith("| Death Cross 50/200 | RVOL 1.1x (Above Avg)"))

    def test_labels_are_html_escaped(self):
        line = screen.fmt_signal("TWST", "A <b> & C", "bullish", None, "x<y")
        self.assertIn("A &lt;b&gt; &amp; C", line)
        self.assertIn("x&lt;y", line)
        self.assertNotIn("<b>", line)

    def test_status_change_links_ticker(self):
        line = screen.fmt_status_change("EXLS", "Trendstruktur", "green")
        self.assertEqual(line, "\U0001F7E2 " + screen.ticker_link("EXLS") + ": Trendstruktur GRUEN")


class CompanyNameTests(unittest.TestCase):
    def setUp(self):
        screen.set_company_names([
            {"ticker": "FLY", "company_name": "Firefly Aerospace Inc"},
            {"ticker": "T", "company_name": "AT&T Inc"},
            {"ticker": "NONAME", "company_name": ""},
        ])
        self.addCleanup(screen.set_company_names, [])

    def test_signal_line_has_link_then_full_company_name(self):
        line = screen.fmt_signal("FLY", "Kursbewegung", "bullish", 1.5, "+5.5%")
        self.assertEqual(
            line,
            "\U0001F7E1 " + screen.ticker_link("FLY") + " (Firefly Aerospace Inc)"
            " | Kursbewegung | RVOL 1.5x (Above Avg) | +5.5%",
        )

    def test_status_change_has_company_name(self):
        line = screen.fmt_status_change("FLY", "Fundamental", "red")
        self.assertEqual(line, "\U0001F534 " + screen.ticker_link("FLY") + " (Firefly Aerospace Inc): Fundamental ROT")

    def test_company_name_is_html_escaped(self):
        self.assertIn("(AT&amp;T Inc)", screen.fmt_signal("T", "Pocket Pivot", "bullish", 2.4))

    def test_missing_or_empty_name_falls_back_to_plain_link(self):
        self.assertEqual(screen.ticker_label("NONAME"), screen.ticker_link("NONAME"))
        self.assertEqual(screen.ticker_label("UNKNOWN"), screen.ticker_link("UNKNOWN"))


class SendMessageTests(unittest.TestCase):
    URL = "https://api.telegram.org/botTOKEN/sendMessage"

    def test_sends_html_with_link_preview_disabled(self):
        text = screen.fmt_signal("TWST", "Pocket Pivot", "bullish", 2.4)
        with mock.patch.object(telegram.requests, "post", return_value=response()) as post:
            telegram.send_message("TOKEN", "123", text)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], self.URL)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["chat_id"], "123")
        self.assertEqual(payload["text"], text)
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(payload["link_preview_options"], {"is_disabled": True})

    def test_markup_rejected_falls_back_to_plain_text_without_tags(self):
        text = "\U0001F7E2 " + screen.ticker_link("TWST") + " | A &amp; B"
        bad = response(400, '{"ok":false,"description":"Bad Request: can\'t parse entities: ..."}')
        with mock.patch.object(telegram.requests, "post", side_effect=[bad, response()]) as post:
            telegram.send_message("TOKEN", "123", text)
        self.assertEqual(post.call_count, 2)
        retry = post.call_args_list[1].kwargs["json"]
        self.assertEqual(retry["text"], "\U0001F7E2 TWST | A & B")
        self.assertNotIn("parse_mode", retry)
        self.assertEqual(retry["link_preview_options"], {"is_disabled": True})

    def test_other_http_errors_raise_without_retry(self):
        with mock.patch.object(telegram.requests, "post", return_value=response(401, "Unauthorized")) as post:
            with self.assertRaises(telegram.TelegramError):
                telegram.send_message("TOKEN", "123", "hi")
        self.assertEqual(post.call_count, 1)

    def test_network_error_does_not_leak_token(self):
        error = requests.ConnectionError(f"failed for {self.URL}")
        with mock.patch.object(telegram.requests, "post", side_effect=error):
            with self.assertRaises(telegram.TelegramError) as ctx:
                telegram.send_message("TOKEN", "123", "hi")
        self.assertNotIn("TOKEN", str(ctx.exception))

    def test_empty_text_sends_nothing(self):
        with mock.patch.object(telegram.requests, "post") as post:
            telegram.send_message("TOKEN", "123", "   ")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
