"""Minimal Telegram Bot API client: sendMessage over HTTP POST, no webhook."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: float = 15.0):
        self._url = API.format(token=token)
        self._chat_id = chat_id
        self._timeout = timeout

    def send(self, text: str) -> None:
        try:
            r = httpx.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            # Never include the URL: it contains the bot token.
            raise TelegramError(f"network error: {type(e).__name__}") from None
        if r.status_code != 200:
            try:
                desc = r.json().get("description", "")
            except ValueError:
                desc = r.text[:200]
            raise TelegramError(f"HTTP {r.status_code}: {desc}")
