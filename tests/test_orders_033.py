"""FunPay Rust v0.3.3: durable notifications, 30s poll, search and role checks."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from bot.orders import Order, OrderStore
from bot.settings import Settings
from bot.plugins import PluginManager
from bot.commands import reply_for, callback_for
from bot import __main__ as service

class FakeAPI:
    def __init__(self, failures=0):
        self.failures = failures
        self.sent = []
    def send(self, uid, content, markup=None):
        if self.failures:
            self.failures -= 1
            raise service.TelegramError('temporary error')
        self.sent.append((uid, content, markup))

class TestReliableOrders(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = OrderStore(root / 'orders.sqlite3')
        self.settings = Settings(100, root / 'settings.json')
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.settings.set_monitor(True)
        self.settings.toggle_order_alert(100)
        self.settings.toggle_order_alert(200)
        self.plugins = PluginManager(root / 'plugins')
        self.started = datetime.now(timezone.utc)

    def message(self, who, command):
        return {'from': {'id': who}, 'chat': {'id': who, 'type': 'private'},
                'text': command}

    def cb(self, who, data):
        return {'from': {'id': who}, 'message': {
            'chat': {'id': who, 'type': 'private'}, 'message_id': 3}, 'data': data}

    def test_pending_deliveries_survive_restart(self):
        self.store.record([Order('OLD12345', 'paid')], recipients=[100])
        self.store.record([Order('NEW12345', 'paid')], recipients=[100, 200])
        store = OrderStore(self.store.filename)
        self.assertEqual(len(store.pending_deliveries()), 2)
        api = FakeAPI(failures=1)
        service.flush_deliveries(store, self.settings, api)
        self.assertEqual(len(store.pending_deliveries()), 1)
        service.flush_deliveries(store, self.settings, api)
        self.assertEqual(len(store.pending_deliveries()), 0)
        self.assertEqual(len(api.sent), 2)
        self.assertTrue(all(x[2]['inline_keyboard'][0][0]['url'].startswith(
            'https://funpay.com/orders/') for x in api.sent))

    def test_paid_closed_refunded_transitions(self):
        self.store.record([Order('OLD12345', 'paid')], recipients=[100])
        self.store.record([Order('OLD12345', 'closed')], recipients=[100])
        self.assertEqual(self.store.pending_deliveries()[0][2], 'status')
        api = FakeAPI()
        service.flush_deliveries(self.store, self.settings, api)
        self.assertIn('closed', api.sent[0][1])
        self.store.record([Order('OLD12345', 'refunded')], recipients=[100])
        self.assertEqual(len(self.store.pending_deliveries()), 1)
        service.flush_deliveries(self.store, self.settings, api)
        self.assertEqual(len(api.sent), 2)
        self.assertIn('refunded', api.sent[1][1])

    def test_disallow_delivery_after_admin_revocation(self):
        self.store.record([Order('OLD12345', 'paid')], recipients=[200])
        self.store.record([Order('NEW12345', 'paid')], recipients=[200])
        self.settings.remove(200)
        api = FakeAPI()
        service.flush_deliveries(self.store, self.settings, api)
        self.assertEqual(api.sent, [])
        self.assertEqual(self.store.pending_deliveries(), [])

    def test_no_double_alert(self):
        self.store.record([], recipients=[100])
        new = Order('NEW12345', 'paid', 'Товар')
        self.store.record([new], recipients=[100])
        self.store.record([new], recipients=[100])
        self.assertEqual(len(self.store.pending_deliveries()), 1)

    def test_history_search_and_no_viewer_access(self):
        self.store.record([Order('ABCD1234','paid','Ключ','BuyerOne')])
        self.assertEqual(self.store.find('BuyerOne')[0].order_id, 'ABCD1234')
        self.assertEqual(self.store.find('ABCD'), self.store.find('Ключ'))
        self.assertEqual(self.store.find('does_not_exist'), [])
        output = reply_for(self.message(100,'/findorder buyer'), 100,
                           self.plugins, self.started, settings=self.settings,
                           orders=self.store)
        self.assertIn('ABCD1234', output)
        viewer = reply_for(self.message(300, '/findorder BuyerOne'), 100,
                           self.plugins, self.started, settings=self.settings,
                           orders=self.store)
        self.assertIn('🔒', viewer)

    def test_testalert_no_purchase_or_network(self):
        output = reply_for(self.message(100,'/testalert'),100,self.plugins,
                           self.started,settings=self.settings,orders=self.store)
        self.assertIn('ТЕСТ', output)
        self.assertIn('Реальной покупки нет', output)
        self.assertEqual(self.store.summary()[0], 0)
        self.assertIn('🔒', reply_for(self.message(300,'/testalert'),100,
                         self.plugins,self.started,settings=self.settings,orders=self.store))

    def test_poll_guard_limits_requests(self):
        guard = service.PollGuard(minimum_interval=60)
        api = FakeAPI()
        calls = []
        def fetch(*a, **kw):
            calls.append(1)
            return []
        service.poll_orders_once(self.store, self.settings, api,
                                 key='A'*32, fetcher=fetch, guard=guard)
        service.poll_orders_once(self.store, self.settings, api,
                                 key='A'*32, fetcher=fetch, guard=guard)
        self.assertEqual(len(calls), 1)

    def test_manual_refresh_and_no_unauthorized_callback(self):
        polls = []
        response = callback_for(self.cb(100, 'o:check'), 100, self.plugins,
                                self.started, settings=self.settings,
                                orders=self.store, poll_now=lambda: polls.append(1))
        self.assertEqual(polls, [1])
        self.assertIn('Заказы', response[0])
        response = callback_for(self.cb(300, 'o:check'), 100, self.plugins,
                                self.started, settings=self.settings,
                                orders=self.store, poll_now=lambda: polls.append(1))
        self.assertIn('🔒', response[0])
        self.assertEqual(polls, [1])

if __name__ == '__main__':
    unittest.main()
