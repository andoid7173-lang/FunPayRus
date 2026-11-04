import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from bot.checkpoint import CheckpointError, UpdateCheckpoint
from bot.commands import reply_for, callback_for
from bot.funpay_probe import ProbeError, status_from_environment, verify
from bot.plugins import PluginManager, PluginStateError


class Response(io.BytesIO):
    def __init__(self, body, url="https://funpay.com/"):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Opener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TestFunPayProbe(unittest.TestCase):
    KEY = "a" * 32

    def html(self, user_id=123, username="Tester"):
        return (f'<html><body data-app-data=\'{{"userId":{user_id}}}\'>'
                f'<div class="user-link-name">{username}</div></body></html>').encode()

    def test_authorized_read_only(self):
        mock = Opener(Response(self.html()))
        text = verify(self.KEY, opener=mock)
        self.assertIn("авторизация подтверждена", text)
        self.assertIn("Tester", text)
        self.assertEqual(mock.requests[0].get_method(), "GET")
        self.assertEqual(mock.requests[0].full_url, "https://funpay.com/")
        self.assertIn(self.KEY, mock.requests[0].get_header("Cookie"))
        self.assertNotIn(self.KEY, text)

    def test_network_client_disables_environment_proxy(self):
        from urllib import request
        html = self.html()
        fake = Opener(Response(html))
        with patch("bot.funpay_probe.request.build_opener", return_value=fake) as factory:
            verify(self.KEY)
        handlers = factory.call_args.args
        self.assertTrue(any(isinstance(x, request.ProxyHandler) and x.proxies == {}
                            for x in handlers))

    def test_missing_auth(self):
        with self.assertRaises(ProbeError):
            verify(self.KEY, opener=Opener(Response(b"<html>logged out</html>")))

    def test_untrusted_redirect_not_followed(self):
        mock = Opener(Response(self.html(), url="https://evil.example/"))
        with self.assertRaises(ProbeError):
            verify(self.KEY, opener=mock)
        self.assertEqual(len(mock.requests), 1)

    def test_redirect_status_no_leak(self):
        err = HTTPError("https://funpay.com/", 302, "redirect", {"Location": "https://evil.example/"}, None)
        result = status_from_environment({"FUNPAY_GOLDEN_KEY": self.KEY}, opener=Opener(err))
        self.assertNotIn(self.KEY, result)
        self.assertIn("перенаправил", result)

    def test_transport_exception_redacted(self):
        mock = Opener(URLError("https://evil.example/" + self.KEY))
        result = status_from_environment({"FUNPAY_GOLDEN_KEY": self.KEY}, opener=mock)
        self.assertNotIn(self.KEY, result)

    def test_invalid_header_rejected(self):
        with self.assertRaises(ProbeError):
            verify(self.KEY, user_agent="x\r\nCookie: leaked")

    def test_no_key(self):
        self.assertIn("локально", status_from_environment({}))

    def test_html_bounded(self):
        with self.assertRaises(ProbeError):
            verify(self.KEY, opener=Opener(Response(b"a" * (2 * 1024 * 1024 + 1))))

    def test_429_handled_without_secrets(self):
        err = HTTPError("https://funpay.com/", 429, "rate limited", {}, None)
        result = status_from_environment({"FUNPAY_GOLDEN_KEY": self.KEY}, opener=Opener(err))
        self.assertIn("частоту", result)
        self.assertNotIn(self.KEY, result)


class TestUpdateCheckpoint(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "offset.json"

    def test_persistent_and_monotonic(self):
        checkpoint = UpdateCheckpoint(self.path)
        self.assertEqual(checkpoint.last_id, -1)
        self.assertTrue(checkpoint.mark(3))
        self.assertFalse(checkpoint.mark(3))
        self.assertFalse(checkpoint.mark(2))
        self.assertEqual(UpdateCheckpoint(self.path).last_id, 3)
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_file_fails_closed(self):
        self.path.write_text("oops")
        with self.assertRaises(CheckpointError):
            UpdateCheckpoint(self.path)

    def test_symlink_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        target = self.path.with_name("elsewhere")
        target.write_text('{"last_update_id":1}')
        self.path.symlink_to(target)
        with self.assertRaises(CheckpointError):
            UpdateCheckpoint(self.path)

    def test_invalid_id_not_saved(self):
        checkpoint = UpdateCheckpoint(self.path)
        self.assertFalse(checkpoint.mark(True))
        self.assertFalse(self.path.exists())


class TestPluginGuard(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.base = self.root / "builtins"
        self.extra = self.root / "external"
        self.base.mkdir()
        self.extra.mkdir()
        p = self.extra / "bad"
        p.mkdir()
        (p / "manifest.json").write_text(json.dumps({
            "name": "Bad", "command": "bad", "enabled": True
        }))
        (p / "plugin.py").write_text("from pathlib import Path\nPath(__file__ + '.executed').write_text('yes')\ndef handle(args): return 'bad'\n")
        self.marker = p / "plugin.py.executed"

    def test_external_code_not_executed_by_default_policy(self):
        mgr = PluginManager(self.base, self.extra, allow_external=False)
        self.assertFalse(self.marker.exists())
        self.assertNotIn("bad", mgr.commands)

    def test_explicit_opt_in_still_supported(self):
        mgr = PluginManager(self.base, self.extra, allow_external=True)
        self.assertIn("bad", mgr.commands)
        self.assertTrue(self.marker.exists())

    def test_plugin_state_permissions(self):
        state = self.root / "plugins.json"
        p = self.base / "demo"
        p.mkdir()
        (p / "manifest.json").write_text(json.dumps({
            "name": "Demo", "command": "demo", "enabled": True
        }))
        (p / "plugin.py").write_text("def handle(args): return 'ok'\n")
        mgr = PluginManager(self.base, state_file=state)
        self.assertTrue(mgr.set_enabled("demo", False))
        self.assertFalse(PluginManager(self.base, state_file=state).commands)
        if os.name == "posix":
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_broken_state_fails_closed(self):
        state = self.root / "plugins.json"
        state.write_text("invalid-json", encoding="utf-8")
        with self.assertRaises(PluginStateError):
            PluginManager(self.base, self.extra, state_file=state)

    def test_symlink_state_fails_closed(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        state = self.root / "plugins.json"
        state.symlink_to(self.root / "missing")
        with self.assertRaises(PluginStateError):
            PluginManager(self.base, self.extra, state_file=state)

    def test_external_plugins_require_explicit_opt_in(self):
        mgr = PluginManager(self.base, self.extra)
        self.assertFalse(self.marker.exists())
        self.assertNotIn("bad", mgr.commands)

    def test_plugin_cannot_shadow_funpay_command(self):
        p = self.base / "shadow"
        p.mkdir()
        (p / "manifest.json").write_text(json.dumps({
            "name": "Shadow", "command": "funpay", "enabled": True
        }))
        (p / "plugin.py").write_text("def handle(args): return 'evil'\n")
        mgr = PluginManager(self.base)
        self.assertNotIn("funpay", mgr.commands)


class TestCommandsSecurity(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plugins = PluginManager(Path(tmp.name))
        from datetime import datetime, timezone
        self.started = datetime.now(timezone.utc)

    def test_only_admin_can_probe(self):
        called = []
        probe = lambda: called.append(1) or "OK"
        unauthorized = {"from": {"id": 8}, "chat": {"id": 8, "type": "private"}, "text": "/funpay"}
        self.assertIsNone(reply_for(unauthorized, 7, self.plugins, self.started, probe))
        self.assertEqual(called, [])
        authorized = {"from": {"id": 7}, "chat": {"id": 7, "type": "private"}, "text": "/funpay"}
        self.assertEqual(reply_for(authorized, 7, self.plugins, self.started, probe), "OK")
        self.assertEqual(called, [1])

    def test_group_callback_forbidden(self):
        callback = {"from": {"id": 7}, "message": {"chat": {"id": -70, "type": "group"}}, "data": "m:funpay"}
        self.assertIsNone(callback_for(callback, 7, self.plugins, self.started, lambda: "OK"))
