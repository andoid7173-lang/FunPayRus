import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from bot.orders import Order, OrderStore, OrderMonitorError, fetch_sales, ORDERS_URL, describe_order
from bot.settings import Settings, SettingsError
from bot.commands import reply_for, callback_for, menu
from bot import __main__ as service
from bot.plugins import PluginManager

VALID_KEY = 'A' * 32


def page(*items, authenticated=True):
    header = ('<body data-app-data=\'{"userId":321}\'><div class="user-link-name">Seller</div>'
              if authenticated else '<body><div>Login</div>')
    return (header + ''.join(items) + '</body>').encode('utf-8')


def card(oid='OD12345', status='paid', description='Лицензионный ключ', buyer='buyer', price='500 ₽'):
    status_class = {'paid': 'info', 'refunded': 'warning', 'closed': ''}[status]
    return (f'<a class="tc-item {status_class}" href="/orders/xxx">'
            f'<div class="tc-order">#{oid}</div>'
            f'<div class="order-desc"><div>{description}</div></div>'
            f'<div class="tc-price">{price}</div>'
            f'<div class="media-user-name"><span>{buyer}</span></div>'
            '</a>')


class FakeResponse:
    def __init__(self, data, url=ORDERS_URL):
        self.data = data
        self.url = url
    def geturl(self):
        return self.url
    def read(self, n):
        return self.data[:n]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None


class FakeOpener:
    def __init__(self, data, url=ORDERS_URL):
        self.data = data
        self.url = url
        self.calls = 0
    def open(self, req, timeout=12):
        self.calls += 1
        self.req = req
        return FakeResponse(self.data, self.url)


class TestHTMLSales(unittest.TestCase):
    def test_parse_paid_closed_refunded(self):
        opener = FakeOpener(page(card(), card('OD12346', 'closed'), card('OD12347', 'refunded')))
        result = fetch_sales(VALID_KEY, opener=opener)
        self.assertEqual([o.status for o in result], ['paid', 'closed', 'refunded'])
        self.assertEqual(result[0].order_id, 'OD12345')
        self.assertEqual(result[0].buyer, 'buyer')
        self.assertIn('500', result[0].price)
        self.assertEqual(opener.req.get_method(), 'GET')
        self.assertEqual(opener.req.full_url, ORDERS_URL)
        self.assertIn(VALID_KEY, opener.req.get_header('Cookie'))

    def test_empty_authorized_page(self):
        self.assertEqual(fetch_sales(VALID_KEY, opener=FakeOpener(page())), [])

    def test_reject_login_redirect_and_bad_key(self):
        with self.assertRaises(OrderMonitorError):
            fetch_sales(VALID_KEY, opener=FakeOpener(page(authenticated=False)))
        with self.assertRaises(OrderMonitorError):
            fetch_sales(VALID_KEY, opener=FakeOpener(page(), 'https://other.example/'))
        with self.assertRaises(OrderMonitorError):
            fetch_sales('bad', opener=FakeOpener(page()))

    def test_reject_large_html(self):
        with self.assertRaises(OrderMonitorError):
            fetch_sales(VALID_KEY, opener=FakeOpener(b'x' * (2*1024*1024 + 1)))

    def test_http_errors_redacted(self):
        class Failing:
            def open(self, req, timeout=12):
                raise HTTPError(req.full_url, 429, 'rate limit', {}, None)
        with self.assertRaises(OrderMonitorError) as caught:
            fetch_sales(VALID_KEY, opener=Failing())
        self.assertIn('частоту', str(caught.exception))
        self.assertNotIn(VALID_KEY, str(caught.exception))

    def test_sanitizes_html_data(self):
        result = fetch_sales(VALID_KEY, opener=FakeOpener(page(card(description='Hello\n\tWorld'))))
        self.assertEqual(result[0].description, 'HelloWorld')
        self.assertNotIn('golden_key', describe_order(result[0]))


class TestOrderStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'orders.sqlite3'
        self.store = OrderStore(self.path)

    def test_first_snapshot_silent_and_new_order_notified_once(self):
        old = Order('ORD10001', 'paid', 'До установки')
        new = Order('ORD10002', 'paid', 'После установки')
        self.assertEqual(self.store.record([old]), [])
        self.assertEqual(self.store.record([old]), [])
        self.assertEqual(self.store.record([old, new]), [new])
        self.assertEqual(self.store.record([old, new]), [])
        count, last, orders = OrderStore(self.path).summary()
        self.assertEqual(count, 2)
        self.assertTrue(last > 0)
        self.assertEqual(len(orders), 2)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_status_changes_without_repeat_new(self):
        paid = Order('ORD10001', 'paid')
        closed = Order('ORD10001', 'closed')
        self.assertEqual(self.store.record([paid]), [])
        self.assertEqual(self.store.record([closed]), [])
        self.assertEqual(self.store.record([closed]), [])
        self.assertEqual(self.store.summary()[2][0].status, 'closed')

    def test_rejected_symlink(self):
        self.path.unlink()
        target = Path(self.tmp.name) / 'real.db'
        target.write_bytes(b'')
        self.path.symlink_to(target)
        with self.assertRaises(OrderMonitorError):
            OrderStore(self.path)

    def test_initial_empty_page_silences_older_events(self):
        self.assertEqual(self.store.record([]), [])
        self.assertEqual(self.store.record([Order('ORD10001', 'paid')]),
                         [Order('ORD10001', 'paid')])


class FakeAPI:
    def __init__(self):
        self.sent = []
    def send(self, user_id, text, reply_markup=None):
        self.sent.append((user_id, text))


class TestOrderIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.store = OrderStore(self.d / 'orders.db')
        self.settings = Settings(100, self.d / 'settings.json')
        self.settings.add(200, 'admin')
        self.settings.add(300, 'viewer')
        self.api = FakeAPI()
        self.plugins = PluginManager(self.d / 'empty')
        self.start = datetime.now(timezone.utc)

    def message(self, uid, text):
        return {'from': {'id': uid}, 'chat': {'id': uid, 'type': 'private'}, 'text': text}

    def callback(self, uid, data):
        return {'from': {'id': uid}, 'message': {'chat': {'id': uid, 'type': 'private'},
                'message_id': 77}, 'data': data}

    def test_opt_in_delivery_and_roles(self):
        fetch_count = [0]
        def mock_fetch(key, user_agent=None):
            fetch_count[0] += 1
            return [Order('ORD10001', 'paid', 'Товар')]
        self.assertEqual(service.poll_orders_once(self.store, self.settings, self.api,
                                                  key=VALID_KEY, fetcher=mock_fetch), 0)
        self.assertEqual(fetch_count[0], 0)
        self.settings.set_monitor(True)
        self.settings.toggle_order_alert(100)
        self.settings.toggle_order_alert(200)
        self.assertFalse(self.settings.toggle_order_alert(300))
        self.assertEqual(service.poll_orders_once(self.store, self.settings, self.api,
                                                  key=VALID_KEY, fetcher=mock_fetch), 0)
        def new_sales(key, user_agent=None):
            return [Order('ORD10001', 'paid'), Order('ORD10002', 'paid', 'Лицензионный ключ')]
        self.assertEqual(service.poll_orders_once(self.store, self.settings, self.api,
                                                  key=VALID_KEY, fetcher=new_sales), 1)
        self.assertEqual([x[0] for x in self.api.sent], [100, 200])
        self.assertTrue(all('ORD10002' in x[1] for x in self.api.sent))
        self.assertEqual(service.poll_orders_once(self.store, self.settings, self.api,
                                                  key=VALID_KEY, fetcher=new_sales), 0)
        self.assertEqual(len(self.api.sent), 2)

    def test_viewer_cannot_toggle_or_read_orders(self):
        self.assertIn('🔒', reply_for(self.message(300, '/orders on'), 100, self.plugins,
                                       self.start, settings=self.settings, orders=self.store))
        response = callback_for(self.callback(300, 'o:toggle'), 100, self.plugins,
                                self.start, settings=self.settings, orders=self.store)
        self.assertIn('🔒', response[0])
        self.assertFalse(self.settings.monitor_enabled())
        self.assertIn('🔒', callback_for(self.callback(300, 'a:orders'), 100, self.plugins,
                                        self.start, settings=self.settings, orders=self.store)[0])
        self.assertNotIn('m:orders', str(menu('viewer')))

    def test_owner_toggle_by_telegram_and_admin_sees_status(self):
        text = reply_for(self.message(100, '/orders on'), 100, self.plugins, self.start,
                         settings=self.settings, orders=self.store)
        self.assertIn('включён', text)
        self.assertTrue(self.settings.monitor_enabled())
        text = reply_for(self.message(200, '/orders'), 100, self.plugins, self.start,
                         settings=self.settings, orders=self.store)
        self.assertIn('Заказы', text)
        self.assertIn('🔒', reply_for(self.message(200, '/orders off'), 100, self.plugins,
                                       self.start, settings=self.settings, orders=self.store))
        self.assertTrue(self.settings.monitor_enabled())
        self.assertFalse(self.settings.toggle_order_alert(300))
        self.assertTrue(Settings(100, self.d / 'settings.json').monitor_enabled())

    def test_legacy_settings_upgraded_safely(self):
        data = {'admins': {'200': 'admin'}, 'startup_alerts': [100]}
        self.settings.filename.write_text(json.dumps(data))
        reloaded = Settings(100, self.settings.filename)
        self.assertFalse(reloaded.monitor_enabled())
        reloaded.set_monitor(True)
        reloaded.toggle_order_alert(100)
        again = Settings(100, self.settings.filename)
        self.assertTrue(again.monitor_enabled())
        self.assertTrue(again.order_alert_enabled(100))
        self.assertTrue(again.startup_enabled(100))
        self.assertEqual(again.role(200), 'admin')

    def test_missing_key_error_does_not_call_network(self):
        self.settings.set_monitor(True)
        self.assertEqual(service.poll_orders_once(self.store, self.settings, self.api,
                                                  key='', fetcher=lambda *a, **kw: 1), 0)
        self.assertIn('Golden Key', self.store.last_error)
        self.assertEqual(self.api.sent, [])


if __name__ == '__main__':
    unittest.main()
