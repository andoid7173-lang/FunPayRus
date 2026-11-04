import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from bot.commands import callback_for, menu, plugin_menu
from bot.plugins import PluginManager

ROOT = Path(__file__).resolve().parents[1]

class TestMenu(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.plugins = PluginManager(ROOT / 'plugins', state_file=self.path)
        self.start = datetime.now(timezone.utc)

    def event(self, user=100, data='m:home', chat_type='private'):
        return {'from': {'id': user}, 'message': {'chat': {'id': 100, 'type': chat_type},
                                                     'message_id': 42}, 'data': data}

    def test_buttons(self):
        self.assertGreaterEqual(len(menu()['inline_keyboard']), 4)
        self.assertTrue(plugin_menu(self.plugins)['inline_keyboard'])

    def test_authorization(self):
        self.assertIsNone(callback_for(self.event(user=101), 100, self.plugins, self.start))
        self.assertIsNone(callback_for(self.event(chat_type='group'), 100, self.plugins, self.start))

    def test_toggle_persistent(self):
        self.assertIn('hello', self.plugins.commands)
        callback_for(self.event(data='p:toggle:hello'), 100, self.plugins, self.start)
        self.assertNotIn('hello', self.plugins.commands)
        self.assertTrue(self.path.exists())
        fresh = PluginManager(ROOT / 'plugins', state_file=self.path)
        self.assertNotIn('hello', fresh.commands)
        callback_for(self.event(data='p:toggle:hello'), 100, fresh, self.start)
        self.assertIn('hello', PluginManager(ROOT / 'plugins', state_file=self.path).commands)
