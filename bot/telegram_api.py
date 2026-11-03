"""Minimal Telegram Bot API transport, stdlib only. Never log URLs with tokens."""
import json
from urllib import request, error


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token: str):
        self._token = token

    def call(self, method: str, payload: dict | None = None):
        url = "https://api.telegram.org/bot" + self._token + "/" + method
        req = request.Request(
            url,
            data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            # HTTPError includes the secret-bearing URL in its default repr.
            raise TelegramError(f"Telegram API HTTP {exc.code}") from None
        except (error.URLError, TimeoutError):
            raise TelegramError("Нет соединения с Telegram API") from None
        except (ValueError, UnicodeError):
            raise TelegramError("Некорректный ответ Telegram API") from None
        if not isinstance(data, dict) or not data.get("ok", False):
            raise TelegramError("Telegram API отказал в выполнении запроса")
        return data.get("result")

    def send(self, chat_id: int, message: str):
        return self.call("sendMessage", {"chat_id": chat_id, "text": message[:4000]})

    def updates(self, offset: int | None):
        payload = {"timeout": 25, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        updates = self.call("getUpdates", payload)
        return updates if isinstance(updates, list) else []
