import unittest
from bot.config import Config

TOKEN = "123456789:" + "A" * 35

class TestConfig(unittest.TestCase):
    def test_accepts_valid(self):
        config = Config.load({"BOT_TOKEN": TOKEN, "ADMIN_ID": "123456"})
        self.assertEqual(config.admin_id, 123456)
    def test_rejects_missing_token(self):
        with self.assertRaises(ValueError):
            Config.load({"ADMIN_ID": "123456"})
    def test_rejects_bad_admin(self):
        with self.assertRaises(ValueError):
            Config.load({"BOT_TOKEN": TOKEN, "ADMIN_ID": "admin"})
