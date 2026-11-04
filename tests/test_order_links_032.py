"""Tests for safe FunPay order links in Telegram."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from bot.orders import Order, OrderStore, order_url, order_button
from bot.settings import Settings
from bot.commands import orders_menu, callback_for
from bot.plugins import PluginManager
from bot import __main__ as service

class TestOrderLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.store = OrderStore(d / 'orders.sqlite3')
        self.settings = Settings(100, d / 'settings.json')
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.plugins = PluginManager(d / 'plugins')
        self.start = datetime.now(timezone.utc)

    def test_direct_url(self):
        self.assertEqual(order_url('BLJR8N7E'), 'https://funpay.com/orders/BLJR8N7E/')
        self.assertEqual(order_button('BLJR8N7E')['url'], order_url('BLJR8N7E'))

    def test_untrusted_ids_never_become_links(self):
        for value in ('', 'A', '../admin', '//evil.com', 'ID123/?key=x',
                      'https://evil.com', 'АБВ1234', 'AB1234\n', 'ID1234%2F', None, 45):
            with self.subTest(value=value):
                self.assertIsNone(order_url(value))
                self.assertIsNone(order_button(value))

    def test_new_sale_sends_button_only_to_subscribers(self):
        self.settings.set_monitor(True)
        self.settings.toggle_order_alert(100)
        self.settings.toggle_order_alert(200)
        class API:
            sent = []
            def send(self, user, message, reply_markup=None):
                self.sent.append((user, message, reply_markup))
        api = API()
        first = lambda key, user_agent=None: [Order('OLD10001', 'paid')]
        second = lambda key, user_agent=None: [Order('OLD10001','paid'),
                                               Order('NEW10002','paid','Тест')]
        self.assertEqual(service.poll_orders_once(self.store, self.settings, api,
                         key='A'*32, fetcher=first), 0)
        self.assertEqual(service.poll_orders_once(self.store, self.settings, api,
                         key='A'*32, fetcher=second), 1)
        self.assertEqual([e[0] for e in api.sent], [100, 200])
        for user, text, markup in api.sent:
            self.assertIn('NEW10002', text)
            self.assertEqual(markup['inline_keyboard'][0][0]['url'],
                             'https://funpay.com/orders/NEW10002/')
            self.assertNotIn('A'*32, str(markup))
        self.assertEqual(service.poll_orders_once(self.store, self.settings, api,
                         key='A'*32, fetcher=second), 0)

    def test_history_order_buttons_role_gated(self):
        self.store.record([Order('OLD10001','paid'), Order('OLD10002','closed')])
        for user in (100, 200):
            markup = orders_menu(self.settings, user, self.store)
            urls = [b['url'] for row in markup['inline_keyboard']
                    for b in row if 'url' in b]
            self.assertEqual(set(urls), {'https://funpay.com/orders/OLD10001/',
                                         'https://funpay.com/orders/OLD10002/'})
            event = {'from': {'id': user}, 'message': {
                     'chat': {'id': user, 'type': 'private'}, 'message_id': 71},
                     'data': 'm:orders'}
            reply = callback_for(event, 100, self.plugins, self.start,
                                 settings=self.settings, orders=self.store)
            self.assertTrue(any('url' in b for row in reply[1]['inline_keyboard']
                                for b in row))
        self.assertFalse(any('url' in b for row in
                             orders_menu(self.settings, 300, self.store)['inline_keyboard']
                             for b in row))
        event = {'from': {'id': 300}, 'message': {
                 'chat': {'id': 300, 'type': 'private'}, 'message_id': 71},
                 'data': 'm:orders'}
        reply = callback_for(event, 100, self.plugins, self.start,
                             settings=self.settings, orders=self.store)
        self.assertIn('🔒', reply[0])
        self.assertFalse(any('url' in b for row in reply[1]['inline_keyboard']
                             for b in row))

    def test_empty_history_has_no_fake_button(self):
        markup = orders_menu(self.settings, 100, self.store)
        self.assertFalse(any('url' in b for row in markup['inline_keyboard']
                             for b in row))
