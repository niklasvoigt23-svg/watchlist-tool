"""Minimal Telegram Bot API sender. No extra package needed, plain POST."""

import requests


class TelegramError(Exception):
    pass


def send_message(bot_token, chat_id, text):
    if not text.strip():
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20)
    except requests.RequestException as e:
        raise TelegramError(f"Telegram request failed: {e}") from e
    if resp.status_code != 200:
        raise TelegramError(f"Telegram send failed (HTTP {resp.status_code}): {resp.text[:200]}")
