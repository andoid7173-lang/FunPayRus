import unittest
from urllib.error import HTTPError
from unittest.mock import patch
from bot.telegram_api import TelegramAPI, TelegramError

class TestTransport(unittest.TestCase):
    def test_http_error_does_not_expose_token(self):
        token = "123456:" + "B" * 35
        api = TelegramAPI(token)
        with patch("bot.telegram_api.request.urlopen", side_effect=HTTPError("https://example.com/"+token, 401, "unauthorized", None, None)):
            with self.assertRaises(TelegramError) as ctx:
                api.call("getMe")
        self.assertNotIn(token, str(ctx.exception))
