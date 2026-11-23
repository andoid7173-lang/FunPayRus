"""v0.3.4 manual-review queue tests."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from bot.commands import callback_for, reply_for
from bot.orders import Order, OrderStore
from bot.plugins import PluginManager
from bot.settings import Settings

class TestManualReviewQueue(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.orders = OrderStore(root / 'orders.sqlite3')
        self.settings = Settings(100, root / 'settings.json')
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.plugins = PluginManager(root / 'plugins')
        self.started = datetime.now(timezone.utc)

    def message(self, uid, text, chat_type='private'):
        return {'from': {'id': uid}, 'chat': {'id': uid, 'type': chat_type}, 'text': text}

    def reply(self, uid, text):
        return reply_for(self.message(uid, text), 100, self.plugins, self.started,
                         settings=self.settings, orders=self.orders)

    def test_first_snapshot_creates_candidates_once(self):
        self.assertEqual(self.orders.record([Order('OLD11111', 'paid')]), [])
        self.assertEqual(self.orders.review_counts(), (1, 0, 0))
        self.orders.record([Order('OLD11111', 'paid'), Order('NEW22222', 'paid')])
        self.orders.record([Order('OLD11111', 'paid'), Order('NEW22222', 'paid')])
        self.assertEqual(self.orders.review_counts(), (2, 0, 0))
        self.assertEqual(len(self.orders.pending_reviews()), 2)
        self.assertEqual(OrderStore(self.orders.filename).review_counts(), (2, 0, 0))

    def test_done_is_idempotent_without_delivery(self):
        self.orders.record([Order('ORD12345', 'paid')])
        self.assertIn('НЕ отправка товара', self.reply(100, '/queue done ORD12345'))
        self.assertEqual(self.orders.review_counts(), (0, 1, 0))
        self.assertIn('уже отмечен', self.reply(100, '/queue done ORD12345'))
        self.assertEqual(self.orders.review_counts(), (0, 1, 0))

    def test_access_and_private_chat_required(self):
        self.orders.record([Order('ORD12345', 'paid')])
        self.assertIn('🔒', self.reply(200, '/queue done ORD12345'))
        self.assertIn('🔒', self.reply(300, '/queue'))
        self.assertIsNone(reply_for(self.message(100, '/queue done ORD12345', 'group'),
                                    100, self.plugins, self.started, settings=self.settings,
                                    orders=self.orders))
        self.assertIn('ORD12345', self.reply(200, '/queue'))
        self.assertEqual(self.orders.review_counts(), (1, 0, 0))

    def test_refund_blocks_reprocessing(self):
        self.orders.record([Order('ORD12345', 'paid')])
        self.orders.record([Order('ORD12345', 'refunded')])
        self.assertEqual(self.orders.review_counts(), (0, 0, 1))
        self.assertIn('заблокирована', self.reply(100, '/queue done ORD12345'))
        self.orders.record([Order('ORD12345', 'paid')])
        self.assertEqual(self.orders.review_counts(), (0, 0, 1))

    def test_untrusted_and_missing_ids(self):
        self.assertEqual(self.orders.mark_reviewed('../etc', 100), 'invalid')
        self.assertEqual(self.orders.mark_reviewed('ORD12345', -1), 'invalid')
        self.assertEqual(self.orders.mark_reviewed('ORD12345', 100), 'missing')
        self.assertIn('не найден', self.reply(100, '/queue done ORD12345'))

    def test_callback_lists_links_without_mutation(self):
        self.orders.record([Order('ORD12345', 'paid')])
        event = {'from': {'id': 100}, 'message': {
                 'chat': {'id': 100, 'type': 'private'}, 'message_id': 1}, 'data': 'm:queue'}
        output, keyboard = callback_for(event, 100, self.plugins, self.started,
                                        settings=self.settings, orders=self.orders)
        self.assertIn('ORD12345', output)
        self.assertTrue(any(b.get('url') == 'https://funpay.com/orders/ORD12345/'
                            for row in keyboard['inline_keyboard'] for b in row))
        self.assertEqual(self.orders.review_counts(), (1, 0, 0))

if __name__ == '__main__':
    unittest.main()
