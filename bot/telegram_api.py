"""Telegram Bot API, standard library only; never leak bot token into logs."""
import json
from urllib import request, error

class TelegramError(Exception):
    pass

class TelegramAPI:
    def __init__(self, token):
        self._token = token

    def call(self, method, payload=None):
        url = "https://api.telegram.org/bot" + self._token + "/" + method
        req = request.Request(url, data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise TelegramError(f"Telegram API HTTP {exc.code}") from None
        except (error.URLError, TimeoutError):
            raise TelegramError("Нет соединения с Telegram API") from None
        except (UnicodeError, ValueError):
            raise TelegramError("Некорректный ответ Telegram API") from None
        if not isinstance(data, dict) or not data.get("ok"):
            raise TelegramError("Telegram API отказал в выполнении запроса")
        return data.get("result")

    def send(self, chat_id, message, reply_markup=None):
        data = {"chat_id": chat_id, "text": message[:4000]}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("sendMessage", data)

    def edit(self, chat_id, message_id, message, reply_markup=None):
        data = {"chat_id": chat_id, "message_id": message_id, "text": message[:4000]}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("editMessageText", data)

    def acknowledge(self, callback_id):
        return self.call("answerCallbackQuery", {"callback_query_id": callback_id})

    def updates(self, offset):
        payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        updates = self.call("getUpdates", payload)
        return updates if isinstance(updates, list) else []
