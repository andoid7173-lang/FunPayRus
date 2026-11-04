import unittest
from datetime import datetime, timezone
from pathlib import Path
from bot.commands import reply_for
from bot.plugins import PluginManager

ROOT = Path(__file__).resolve().parents[1]

class TestBot(unittest.TestCase):
    def setUp(self):
        self.plugins = PluginManager(ROOT / 'plugins')
        self.start = datetime.now(timezone.utc)

    def incoming(self, user, command, chat_type='private', chat_id=None):
        return {'from': {'id': user}, 'chat': {'id': chat_id if chat_id is not None else user,
                'type': chat_type}, 'text': command}

    def test_private_admin(self):
        self.assertIn('FunPay Rust', reply_for(self.incoming(10, '/start'), 10, self.plugins, self.start))

    def test_block_other_user(self):
        self.assertIsNone(reply_for(self.incoming(11, '/status'), 10, self.plugins, self.start))

    def test_block_group(self):
        self.assertIsNone(reply_for(self.incoming(10, '/status', 'group', -100), 10, self.plugins, self.start))

    def test_plugins_loaded(self):
        self.assertIn('/hello', reply_for(self.incoming(10, '/plugins'), 10, self.plugins, self.start))
        self.assertIn('Привет', reply_for(self.incoming(10, '/hello'), 10, self.plugins, self.start))

    def test_ping(self):
        self.assertIn('Pong', reply_for(self.incoming(10, '/ping'), 10, self.plugins, self.start))
