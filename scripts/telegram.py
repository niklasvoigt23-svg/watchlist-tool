"""Minimal Telegram Bot API sender. No extra package needed, plain POST."""

import html
import re

import requests


class TelegramError(Exception):
    pass


def _post(url, payload):
    try:
        return requests.post(url, json=payload, timeout=20)
    except requests.RequestException as e:
        raise TelegramError(f"Telegram request failed: {type(e).__name__}") from e


def _plain(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def send_message(bot_token, chat_id, text, parse_mode="HTML"):
    """Sends text. With parse_mode "HTML" the text may contain <a href> links; &, < and > in
    normal text must already be escaped. The link preview is always off, so a message with a
    chart link stays a dry one-liner instead of growing a preview card.

    If Telegram rejects the markup (HTTP 400 "can't parse entities"), the message is sent
    again as plain text without tags, so an alert is never lost over a formatting bug."""
    if not text.strip():
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    preview_off = {"is_disabled": True}

    payload = {"chat_id": chat_id, "text": text, "link_preview_options": preview_off}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    resp = _post(url, payload)

    if resp.status_code == 400 and parse_mode and "parse entities" in resp.text:
        resp = _post(url, {"chat_id": chat_id, "text": _plain(text), "link_preview_options": preview_off})

    if resp.status_code != 200:
        raise TelegramError(f"Telegram send failed (HTTP {resp.status_code}): {resp.text[:200]}")
