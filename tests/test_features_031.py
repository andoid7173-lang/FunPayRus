import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bot.commands import allowed, callback_for, reply_for, menu
from bot.diagnostics import demo_screen, report
from bot.plugins import PluginManager
from bot.settings import Settings, SettingsError

ROOT = Path(__file__).resolve().parents[1]


class TestSecurityAndRoles(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'settings.json'
        self.settings = Settings(100, self.file)
        self.plugins = PluginManager(ROOT / 'plugins')
        self.start = datetime.now(timezone.utc)

    def msg(self, uid, command, chat_type='private', chat_id=None):
        return {'from': {'id': uid}, 'chat': {'id': uid if chat_id is None else chat_id,
                'type': chat_type}, 'text': command}

    def cb(self, uid, data):
        return {'from': {'id': uid}, 'message': {'chat': {'id': uid, 'type': 'private'},
                'message_id': 42}, 'data': data}

    def reply(self, uid, cmd):
        return reply_for(self.msg(uid, cmd), 100, self.plugins, self.start,
                         settings=self.settings, funpay_status=lambda: 'FunPay OK')

    def callback(self, uid, data):
        return callback_for(self.cb(uid, data), 100, self.plugins, self.start,
                            settings=self.settings, funpay_status=lambda: 'FunPay OK')

    def test_add_roles_persistent(self):
        self.assertIn('✅', self.reply(100, '/addadmin 200 admin'))
        self.assertEqual(Settings(100, self.file).role(200), 'admin')
        self.assertIn('✅', self.reply(100, '/addadmin 300'))
        self.assertEqual(Settings(100, self.file).role(300), 'viewer')
        self.assertIn('✅', self.reply(100, '/removeadmin 200'))
        self.assertIsNone(Settings(100, self.file).role(200))

    def test_roles_cannot_escalate_or_toggle_plugins(self):
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.assertIn('Только владелец', self.reply(200, '/addadmin 400 admin'))
        self.assertIn('Только владелец', self.reply(300, '/removeadmin 200'))
        self.assertIn('Только владелец', self.callback(200, 'p:toggle:hello')[0])
        self.assertIn('hello', self.plugins.commands)
        self.assertIn('ограничен', self.reply(200, '/plugins'))
        self.assertIn('доступна администраторам', self.reply(300, '/funpay'))
        self.assertIsNone(self.reply(300, '/hello'))

    def test_disallow_wrong_chat_and_impersonated_sender(self):
        self.settings.add(200, 'admin')
        self.assertFalse(allowed(self.msg(200, '/status', 'group', -100), 100, self.settings))
        self.assertFalse(allowed(self.msg(200, '/status', 'private', 100), 100, self.settings))
        self.assertFalse(allowed(self.msg(201, '/status'), 100, self.settings))
        self.assertIsNone(reply_for(self.msg(201, '/status'), 100, self.plugins, self.start,
                                     settings=self.settings))

    def test_notification_persistence_and_owner_is_immutable(self):
        self.settings.add(200, 'viewer')
        self.assertFalse(self.settings.remove(100))
        self.assertTrue(self.settings.toggle_startup(200))
        self.assertTrue(Settings(100, self.file).startup_enabled(200))
        self.assertIn('включено', self.callback(200, 'm:notifications')[0])
        self.callback(200, 'a:startup')
        self.assertFalse(Settings(100, self.file).startup_enabled(200))
        self.assertEqual(self.settings.alert_recipients(), [])
        self.assertTrue(self.settings.toggle_startup(200))
        self.settings.remove(200)
        self.assertEqual(self.settings.alert_recipients(), [])

    def test_roles_cannot_add_owner_or_exceed_limit(self):
        self.assertFalse(self.settings.add(100, 'admin'))
        for i in range(101, 106):
            self.assertTrue(self.settings.add(i, 'viewer'))
        self.assertFalse(self.settings.add(106))
        self.assertEqual(self.settings.role(100), 'owner')

    def test_invalid_and_symlink_settings_fail_closed(self):
        self.file.write_text('{"admins":{"100":"admin"},"startup_alerts":[]}', encoding='utf8')
        with self.assertRaises(SettingsError):
            Settings(100, self.file)
        self.file.unlink()
        target = self.file.with_name('target.json')
        target.write_text('{"admins":{},"startup_alerts":[]}', encoding='utf8')
        if hasattr(os, 'symlink'):
            self.file.symlink_to(target)
            with self.assertRaises(SettingsError):
                Settings(100, self.file)

    def test_no_auto_enable_and_file_permissions(self):
        self.settings.add(200)
        obj = json.loads(self.file.read_text(encoding='utf8'))
        self.assertEqual(obj, {'admins': {'200': 'viewer'}, 'startup_alerts': []})
        if os.name == 'posix':
            self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_ui_and_diagnostics_are_explicit(self):
        self.assertGreaterEqual(len(menu()['inline_keyboard']), 4)
        self.assertIn('ДЕМОНСТРАЦИЯ', demo_screen())
        self.assertIn('ключ не указан', report(self.plugins, state_dir=Path(self.tmp.name), env={}))
        self.assertNotIn('FakeSecret123', report(self.plugins, state_dir=Path(self.tmp.name),
                                                 env={'FUNPAY_GOLDEN_KEY': 'FakeSecret123'}))
        self.assertIn('запущен', self.reply(200 if self.settings.role(200) else 100, '/setup'))

class TestServiceDispatchToSecondAdmin(unittest.TestCase):
    def test_second_admin_receives_own_reply_and_startup_notice(self):
        from bot import __main__ as service
        from types import SimpleNamespace
        from unittest.mock import patch

        class FakeTelegram:
            seen = []
            def __init__(self, token):
                self.token = token
            def call(self, method, payload=None):
                return {'id': 99}
            def updates(self, offset):
                service.RUNNING = False
                return [{'update_id': 70, 'message': {'from': {'id': 200},
                         'chat': {'id': 200, 'type': 'private'}, 'text': '/status'}}]
            def send(self, chat_id, message, reply_markup=None):
                self.seen.append((chat_id, message))
            def acknowledge(self, *args):
                pass

        with TemporaryDirectory() as tmp:
            settings = Settings(100, Path(tmp) / 'settings.json')
            settings.add(200, 'admin')
            settings.toggle_startup(200)
            old_dir, old_running = service.STATE_DIR, service.RUNNING
            self.addCleanup(setattr, service, 'STATE_DIR', old_dir)
            self.addCleanup(setattr, service, 'RUNNING', old_running)
            service.STATE_DIR = Path(tmp)
            FakeTelegram.seen = []
            with patch.object(service.Config, 'load', return_value=SimpleNamespace(bot_token='fake', admin_id=100)), \
                 patch.object(service, 'TelegramAPI', FakeTelegram), \
                 patch.object(service.signal, 'signal'):
                service.RUNNING = True
                self.assertEqual(service.main(), 0)
            self.assertEqual([uid for uid, _ in FakeTelegram.seen], [200, 200])
            self.assertIn('запущен', FakeTelegram.seen[0][1])
            self.assertIn('FunPay Rust', FakeTelegram.seen[1][1])
