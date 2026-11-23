"""Offline Outlook tests: metadata-only Graph and durable, nonsecret outbox."""
import base64
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from bot.mail_outlook import (GraphError, classify, fetch_since, safe_graph_url,
                              received_epoch)
from bot.mail_service import MailService
from bot.orders import Order, OrderStore


class TestGraphAdapter(unittest.TestCase):
    def test_only_exact_sender_domains(self):
        self.assertEqual(classify('hello@steampowered.com'), 'steam')
        self.assertEqual(classify('hello@mail.steampowered.com'), 'steam')
        for addr in ('x@steampowered.com.evil.test', 'x@fakesteampowered.com',
                     'x@stéampowered.com', 'not-a-mail', 'x@@steampowered.com'):
            self.assertIsNone(classify(addr))

    def test_url_guard_and_iso_time(self):
        self.assertTrue(safe_graph_url(
            'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=a'))
        for url in ('http://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages',
                    'https://evil.test/v1.0/me/mailFolders/inbox/messages',
                    'https://graph.microsoft.com/v1.0/me/messages',
                    'https://graph.microsoft.com.evil.test/v1.0/me/mailFolders/inbox/messages'):
            self.assertFalse(safe_graph_url(url))
        self.assertIsNone(received_epoch('not a datetime'))
        self.assertIsNotNone(received_epoch('2026-11-24T10:01:00Z'))

    def test_pagination_and_no_subject(self):
        now = int(time.time())
        from datetime import datetime, timezone
        timestamp = datetime.fromtimestamp(now, timezone.utc).isoformat().replace('+00:00', 'Z')
        first = {'value': [
            {'id': 'secret-message-id', 'receivedDateTime': timestamp,
             'from': {'emailAddress': {'address': 'updates@steampowered.com'}},
             'subject': 'DO NOT COPY SECRET 123456'}],
            '@odata.nextLink':
              'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=abc'}
        second = {'value': [{'id': 'another', 'receivedDateTime': timestamp,
                             'from': {'emailAddress': {'address': 'x@epicgames.com'}}}]}
        calls = []
        def fake_get(url, token):
            calls.append((url, token))
            return first if len(calls) == 1 else second
        found = fetch_since('dummy-token', now - 5, http_get=fake_get)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0], ('secret-message-id', now, 'steam'))
        self.assertEqual(found[1][2], 'epic')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('DO NOT COPY', repr(found))
        self.assertNotIn('subject', calls[0][0])

    def test_malicious_next_link_fails_before_advancing(self):
        now = int(time.time())
        def getter(url, token):
            return {'value': [], '@odata.nextLink':
                    'https://attacker.test/v1.0/me/mailFolders/inbox/messages'}
        with self.assertRaises(GraphError):
            fetch_since('dummy', now - 10, http_get=getter)

    def test_truncated_page_fails_closed(self):
        now = int(time.time())
        def getter(url, token):
            return {'value': [], '@odata.nextLink': url + '&page=again'}
        with self.assertRaises(GraphError):
            fetch_since('dummy', now - 10, http_get=getter)


class TestMailService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.dbfile = root / 'orders.sqlite3'
        self.orders = OrderStore(self.dbfile)
        self.orders.record([Order('ORD12345', 'paid')])
        self.keyfile = root / 'mail.key'
        self.keyfile.write_bytes(base64.urlsafe_b64encode(b'k' * 32))
        os.chmod(self.keyfile, 0o600)
        self.now = int(time.time())
        self.svc = MailService(self.dbfile, key_file=self.keyfile,
                               clock=lambda: self.now)
        self.svc.enable()

    def test_first_sync_is_silent_and_persistent(self):
        get = lambda *_: self.fail('initial sync must not contact Graph')
        self.assertEqual(self.svc.poll('client', fetcher=get), 'initialized')
        self.assertEqual(self.svc.status()['pending'], 0)
        restarted = MailService(self.dbfile, key_file=self.keyfile,
                                clock=lambda: self.now)
        self.assertTrue(restarted.status()['initialized'])
        self.assertTrue(restarted.is_enabled())

    def test_outbox_duplicate_and_retry(self):
        self.svc.poll('client', fetcher=lambda *_: self.fail('first'))
        event = ('opaque-secret-id', self.now, 'steam')
        with patch('bot.mail_service.access_token', return_value='local-token'):
            self.assertEqual(self.svc.poll(
                'client', fetcher=lambda token, since: [event]), 'ok')
            self.assertEqual(self.svc.poll(
                'client', fetcher=lambda token, since: [event]), 'ok')
        self.assertEqual(self.svc.status()['pending'], 1)
        def failed_send(_):
            raise ConnectionError('Telegram unavailable')
        with self.assertRaises(ConnectionError):
            self.svc.flush(failed_send)
        self.assertEqual(self.svc.status()['pending'], 1)
        messages = []
        self.assertEqual(self.svc.flush(messages.append), 1)
        self.assertEqual(self.svc.flush(messages.append), 0)
        self.assertEqual(len(messages), 1)
        self.assertIn('Steam', messages[0])
        self.assertNotIn('opaque-secret-id', messages[0])

    def test_truncated_page_never_advances_state(self):
        self.svc.poll('client')
        with self.svc.connect() as db:
            before = db.execute("SELECT value FROM mail_monitor_state WHERE name='since'").fetchone()
        with patch('bot.mail_service.access_token', return_value='local-token'):
            with self.assertRaises(Exception):
                self.svc.poll('client', fetcher=lambda *_: (_ for _ in ()).throw(GraphError(429)))
        with self.svc.connect() as db:
            after = db.execute("SELECT value FROM mail_monitor_state WHERE name='since'").fetchone()
        self.assertEqual(before, after)

    def test_disable_does_not_touch_funpay(self):
        self.svc.poll('client')
        self.svc.disable()
        self.assertEqual(self.svc.poll('client'), 'disabled')
        self.assertEqual(self.orders.summary()[0], 1)


if __name__ == '__main__':
    unittest.main()
