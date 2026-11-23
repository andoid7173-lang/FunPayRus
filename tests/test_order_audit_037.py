"""Owner verification + audit integrated with existing FunPayRus SQLite state."""
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.commands import reply_for
from bot.order_audit import AuditStore
from bot.orders import Order, OrderStore
from bot.plugins import PluginManager
from bot.settings import Settings
from bot.stock import StockStore


class AuditIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.orders = OrderStore(root / 'orders.sqlite3')
        self.stock = StockStore(self.orders.filename)
        self.audit = AuditStore(self.orders.filename)
        self.settings = Settings(100, root / 'settings.json')
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.plugins = PluginManager(root / 'plugins')
        self.started = datetime.now(timezone.utc)
        self.orders.record([Order('ORD11111', 'paid')])

    def command(self, uid, text, chat_type='private'):
        return reply_for(
            {'from': {'id': uid}, 'chat': {'id': uid, 'type': chat_type},
             'text': text}, 100, self.plugins, self.started,
            settings=self.settings, orders=self.orders
        )

    def test_verification_required_before_reserve(self):
        self.stock.add('DIGITAL', 'license-123')
        self.assertEqual(self.stock.reserve('ORD11111', 'DIGITAL'), 'unverified')
        self.assertIn('/verify', self.command(100, '/stock reserve ORD11111 DIGITAL'))
        self.assertIn('🔒', self.command(200, '/verify ORD11111'))
        self.assertIn('🔒', self.command(300, '/audit ORD11111'))
        self.assertIsNone(self.command(100, '/verify ORD11111', 'group'))
        self.assertIn('Проверка зафиксирована', self.command(100, '/verify ORD11111'))
        self.assertIn('уже отмечен', self.command(100, '/verify ORD11111'))
        self.assertEqual(self.stock.reserve('ORD11111', 'DIGITAL'), 'reserved')
        self.assertEqual(self.stock.reserve('ORD11111', 'DIGITAL'), 'already')
        self.assertEqual(self.stock.complete('ORD11111', 100), 'done')
        self.assertEqual(self.orders.review_counts(), (0, 1, 0))
        history = self.audit.history('ORD11111')
        self.assertEqual([x[0] for x in history], ['issued', 'reserved', 'verified'])
        self.assertNotIn('license-123', self.command(100, '/audit ORD11111'))

    def test_refund_revokes_authorization_and_retain_reserved_code(self):
        self.stock.add('DIGITAL', 'license-123')
        self.audit.verify('ORD11111', 100)
        self.stock.reserve('ORD11111', 'DIGITAL')
        self.orders.record([Order('ORD11111', 'refunded')])
        self.assertEqual(self.orders.review_counts(), (0, 0, 1))
        self.assertEqual(self.stock.reservation('ORD11111'), ('DIGITAL', 'reserved'))
        self.assertEqual(self.stock.summary()['DIGITAL']['available'], 0)
        self.assertEqual(self.stock.complete('ORD11111', 100), 'blocked')
        self.assertIn('verification_revoked', [x[0] for x in self.audit.history('ORD11111')])
        self.orders.record([Order('ORD11111', 'paid')])
        self.assertEqual(self.stock.reserve('ORD11111', 'DIGITAL'), 'already')
        self.assertEqual(self.audit.verify('ORD11111', 100), 'blocked')
        self.assertEqual(self.stock.void('ORD11111'), 'void')
        self.assertEqual(self.stock.summary()['DIGITAL']['available'], 0)

    def test_migrations_and_concurrent_verification(self):
        results = []
        def verify():
            results.append(AuditStore(self.orders.filename).verify('ORD11111', 100))
        threads = [threading.Thread(target=verify) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count('verified'), 1)
        self.assertEqual(results.count('already'), 7)
        self.assertEqual([x[0] for x in self.audit.history('ORD11111')], ['verified'])
        with sqlite3.connect(self.orders.filename) as db:
            self.assertEqual(db.execute(
                'SELECT count(*) FROM manual_verifications').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
