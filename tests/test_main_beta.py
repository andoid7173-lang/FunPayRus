"""End-to-end dry run of Telegram dispatch without network or VPS access."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from bot import __main__ as service


class FakeTelegram:
    delivered = []
    offsets = []

    def __init__(self, token):
        assert token == "fake"

    def call(self, method, payload=None):
        assert method == "getMe"
        return {"id": 99}

    def updates(self, offset):
        self.offsets.append(offset)
        service.RUNNING = False
        return [{"update_id": 50, "message": {
            "from": {"id": 7}, "chat": {"id": 7, "type": "private"},
            "text": "/ping"
        }}]

    def send(self, chat_id, message, reply_markup):
        self.delivered.append((chat_id, message))

    def acknowledge(self, callback_id):
        pass


class TestService(unittest.TestCase):
    def test_resumed_restart_does_not_repeat_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = service.STATE_DIR
            old_running = service.RUNNING
            self.addCleanup(setattr, service, "STATE_DIR", old_dir)
            self.addCleanup(setattr, service, "RUNNING", old_running)
            service.STATE_DIR = Path(tmp)
            FakeTelegram.delivered = []
            FakeTelegram.offsets = []
            with patch.object(service.Config, "load", return_value=SimpleNamespace(bot_token="fake", admin_id=7)), \
                 patch.object(service, "TelegramAPI", FakeTelegram), \
                 patch.object(service.signal, "signal"):
                service.RUNNING = True
                self.assertEqual(service.main(), 0)
                self.assertEqual(len(FakeTelegram.delivered), 1)
                self.assertEqual(FakeTelegram.offsets, [0])
                service.RUNNING = True
                self.assertEqual(service.main(), 0)
                self.assertEqual(len(FakeTelegram.delivered), 1)
                self.assertEqual(FakeTelegram.offsets, [0, 51])
