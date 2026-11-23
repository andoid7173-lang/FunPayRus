"""Offline tests for owner operator controls and zero-content platform events."""
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.commands import reply_for, callback_for
from bot.orders import Order, OrderStore
from bot.operator import OperatorStore
from bot.mail_events import MailEventStore
from bot.plugins import PluginManager
from bot.settings import Settings
from bot.stock import StockStore


class TestOperatorTools(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)
        self.orders = OrderStore(path / 'orders.sqlite3')
        self.settings = Settings(100, path / 'settings.json')
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.plugins = PluginManager(path / 'plugins')
        self.started = datetime.now(timezone.utc)
        self.orders.record([Order('ORD99999', 'paid')])

    def reply(self, uid, text):
        return reply_for({'from': {'id': uid}, 'chat': {'id': uid, 'type': 'private'},
                          'text': text}, 100, self.plugins, self.started,
                         orders=self.orders, settings=self.settings)

    def test_note_is_private_and_requires_known_order(self):
        self.assertIn('сохранена', self.reply(100, '/note ORD99999 Отправить вручную'))
        self.assertIn('Отправить вручную', self.reply(100, '/note ORD99999'))
        self.assertIn('🔒', self.reply(200, '/note ORD99999'))
        self.assertIn('🔒', self.reply(300, '/note ORD99999 текст'))
        self.assertIn('не найден', self.reply(100, '/note OTHER999 текст'))
        self.assertIn('удалена', self.reply(100, '/note clear ORD99999'))
        self.assertIsNone(OperatorStore(self.orders.filename).note('ORD99999'))

    def test_thresholds_and_stats(self):
        stock = StockStore(self.orders.filename)
        stock.add('GAMEKEY', 'test-license-unique')
        self.assertIn('GAMEKEY', self.reply(100, '/lowstock'))
        self.assertIn('обновлён', self.reply(100, '/lowstock set GAMEKEY 0'))
        self.assertNotIn('GAMEKEY', self.reply(100, '/lowstock'))
        self.assertIn('Сводка', self.reply(100, '/stats'))
        self.assertIn('🔒', self.reply(200, '/stats'))
        self.assertNotIn('test-license-unique', self.reply(100, '/stats'))

    def test_platform_center_is_dormant(self):
        message = self.reply(100, '/platforms')
        self.assertIn('НЕ подключены', message)
        self.assertIn('0', message)
        self.assertIn('🔒', self.reply(200, '/platforms'))
        cb = {'from': {'id': 100}, 'message': {
            'chat': {'id': 100, 'type': 'private'}, 'message_id': 22},
            'data': 'm:platforms'}
        response = callback_for(cb, 100, self.plugins, self.started,
                                orders=self.orders, settings=self.settings)
        self.assertIn('Outlook', response[0])


class TestMailEvents(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'orders.sqlite3'
        OrderStore(path)
        self.mail = MailEventStore(path, hash_key=b'a' * 32)

    def test_deduplicate_without_storing_raw_identifier(self):
        now = int(time.time())
        data = {'provider': 'outlook', 'platform': 'steam',
                'kind': 'security_notice', 'opaque_id': 'sensitive-provider-unique-id',
                'received_at': now}
        self.assertEqual(self.mail.ingest(**data), 'recorded')
        self.assertEqual(self.mail.ingest(**data), 'duplicate')
        total, counts = self.mail.summary()
        self.assertEqual(total, 1)
        self.assertEqual(counts[0][0], 'outlook')
        with self.mail.connect() as db:
            row = db.execute('SELECT fingerprint FROM mail_events').fetchone()
        self.assertNotIn('sensitive-provider-unique-id', row[0])
        self.assertEqual(len(row[0]), 64)

    def test_reject_untrusted_inputs_and_old_events(self):
        now = int(time.time())
        self.assertEqual(self.mail.ingest(
            provider='outlook', platform='../steam', kind='other',
            opaque_id='id', received_at=now), 'invalid')
        self.assertEqual(self.mail.ingest(
            provider='outlook', platform='steam', kind='other',
            opaque_id='id', received_at=now - 90000), 'stale')
        self.assertEqual(MailEventStore(self.mail.filename).ingest(
            provider='outlook', platform='steam', kind='other',
            opaque_id='id', received_at=now), 'not_configured')


if __name__ == '__main__':
    unittest.main()
