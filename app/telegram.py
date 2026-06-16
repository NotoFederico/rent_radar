from __future__ import annotations

import os
import requests


class TelegramNotifier:
    """Envía mensajes a uno o más chats vía Telegram Bot API."""

    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN no encontrado en el entorno")

        chat_ids_raw = os.getenv("TELEGRAM_CHAT_ID", "")
        self.chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
        if not self.chat_ids:
            raise ValueError("TELEGRAM_CHAT_ID no encontrado en el entorno")

        self._url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send(self, text: str, disable_preview: bool = True) -> bool:
        """Envía `text` a todos los chats configurados. Retorna True si todos OK."""
        ok = True
        for chat_id in self.chat_ids:
            try:
                r = requests.post(
                    self._url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": disable_preview,
                    },
                    timeout=10,
                )
                if r.status_code != 200:
                    print(f"  Telegram error {r.status_code} → chat {chat_id}: {r.text[:120]}")
                    ok = False
            except Exception as exc:
                print(f"  Telegram excepcion → chat {chat_id}: {exc}")
                ok = False
        return ok
