"""Inventory and operator commands: no network, no FunPay writes."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.commands import reply_for
from bot.orders import Order, OrderStore
from bot.plugins import PluginManager
from bot.settings import Settings
from bot.stock import StockStore


class TestStock(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.orders = OrderStore(root / 'orders.sqlite3')
        self.stock = StockStore(self.orders.filename)
        self.settings = Settings(100, root / 'settings.json')
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.plugins = PluginManager(root / 'plugins')
        self.started = datetime.now(timezone.utc)
        self.orders.record([Order('ORD11111', 'paid')])
        self.orders.record([Order('ORD11111', 'paid'), Order('ORD22222', 'paid')])

    def command(self, uid, text):
        return reply_for(
            {'from': {'id': uid}, 'chat': {'id': uid, 'type': 'private'}, 'text': text},
            100, self.plugins, self.started, settings=self.settings, orders=self.orders
        )

    def test_atomic_reserve_and_issue(self):
        self.assertEqual(self.stock.add('GAMEKEY', 'secret-value-001'), 'added')
        self.assertEqual(self.stock.add('GAMEKEY', 'secret-value-002'), 'added')
        self.assertEqual(self.stock.add('GAMEKEY', 'secret-value-002'), 'duplicate')
        self.assertEqual(self.stock.reserve('ORD11111', 'GAMEKEY'), 'reserved')
        self.assertEqual(self.stock.reserve('ORD11111', 'GAMEKEY'), 'already')
        self.assertEqual(self.stock.reserve('ORD11111', 'OTHER'), 'conflict')
        self.assertEqual(self.stock.reserve('ORD22222', 'GAMEKEY'), 'reserved')
        self.assertEqual(self.stock.summary()['GAMEKEY']['available'], 0)
        self.assertEqual(self.stock.secret_for_local_terminal('ORD11111'), 'secret-value-001')
        self.assertEqual(self.stock.complete('ORD11111', 100), 'done')
        self.assertEqual(self.stock.complete('ORD11111', 100), 'already')
        self.assertIsNone(self.stock.secret_for_local_terminal('ORD11111'))
        self.assertEqual(self.orders.review_counts(), (1, 1, 0))

    def test_missing_and_refunded_not_reservable(self):
        self.assertEqual(self.stock.add('GAMEKEY', 'secret-value-001'), 'added')
        self.assertEqual(self.stock.reserve('NO_ORDER', 'GAMEKEY'), 'missing')
        self.orders.record([Order('ORD22222', 'refunded')])
        self.assertEqual(self.stock.reserve('ORD22222', 'GAMEKEY'), 'blocked')
        self.assertEqual(self.stock.summary()['GAMEKEY']['available'], 1)

    def test_void_reserved_code_permanently(self):
        self.stock.add('GAMEKEY', 'secret-value-001')
        self.assertEqual(self.stock.reserve('ORD11111', 'GAMEKEY'), 'reserved')
        self.assertEqual(self.stock.void('ORD11111'), 'void')
        self.assertEqual(self.stock.void('ORD11111'), 'already')
        self.assertEqual(self.stock.reserve('ORD11111', 'GAMEKEY'), 'already')
        self.assertEqual(self.stock.summary()['GAMEKEY']['void'], 1)

    def test_telegram_discloses_no_codes_and_roles(self):
        self.stock.add('GAMEKEY', 'very-secret-value')
        self.assertIn('📚', self.command(100, '/stock'))
        self.assertNotIn('very-secret-value', self.command(100, '/stock'))
        self.assertIn('🔒', self.command(200, '/stock reserve ORD11111 GAMEKEY'))
        self.assertIn('🔒', self.command(300, '/stock'))
        response = self.command(100, '/stock reserve ORD11111 GAMEKEY')
        self.assertIn('зарезервирован', response)
        self.assertNotIn('very-secret-value', response)
        self.assertNotIn('very-secret-value', self.command(100, '/stock issue ORD11111'))
        self.assertEqual(self.stock.summary()['GAMEKEY']['issued'], 1)

    def test_invalid_codes_and_ids(self):
        self.assertEqual(self.stock.add('x', 'secret'), 'invalid')
        self.assertEqual(self.stock.add('GAMEKEY', 'bad\nvalue'), 'invalid')
        self.assertEqual(self.stock.reserve('../etc', 'GAMEKEY'), 'invalid')
        self.assertEqual(self.stock.complete('../etc', 100), 'invalid')
        self.assertEqual(self.stock.void('../etc'), 'invalid')


if __name__ == '__main__':
    unittest.main()
