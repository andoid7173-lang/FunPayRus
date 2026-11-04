"""Read-only experimental FunPay sign-in check.

Only GET https://funpay.com/; never follows redirects with credentials,
never sends the golden_key to Telegram, doesn't automate transactions.
FunPay does not provide a stable public API: parsing may stop working.
"""
import html
from html.parser import HTMLParser
import json
import os
import re
import ssl
import time
from urllib import error, request

_KEY = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
_MAX_HTML_BYTES = 2 * 1024 * 1024
_CACHE = {"until": 0.0, "response": ""}


class ProbeError(Exception):
    """Known, deliberately non-sensitive failure message."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _ProfileHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_user_name = False
        self.username = ""
        self.app_data = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "body":
            self.app_data = attrs.get("data-app-data")
        if tag == "div" and "user-link-name" in attrs.get("class", "").split():
            self.in_user_name = True

    def handle_endtag(self, tag):
        if tag == "div":
            self.in_user_name = False

    def handle_data(self, data):
        if self.in_user_name and not self.username:
            self.username = data.strip()[:60]


def verify(golden_key, opener=None, timeout=10, user_agent=None):
    """Returns a short success string or raises a safe ProbeError.

    An injected opener makes this fully testable with no external requests.
    """
    if not isinstance(golden_key, str) or not _KEY.fullmatch(golden_key):
        raise ProbeError("Golden Key не настроен или имеет неверный формат")
    if user_agent is not None and (not isinstance(user_agent, str) or
                                   len(user_agent) > 300 or any(c in user_agent for c in "\r\n")):
        raise ProbeError("Некорректный User-Agent")
    headers = {
        "Cookie": "golden_key=" + golden_key,
        "User-Agent": user_agent or "Mozilla/5.0 FunPayRus/0.3.0b1 (read-only test)",
        "Accept": "text/html",
        "Cache-Control": "no-store",
    }
    req = request.Request("https://funpay.com/", headers=headers, method="GET")
    client = opener if opener is not None else request.build_opener(request.ProxyHandler({}), _NoRedirect())
    try:
        with client.open(req, timeout=timeout) as resp:
            if resp.geturl() != "https://funpay.com/":
                raise ProbeError("FunPay перенаправил запрос; проверка остановлена")
            data = resp.read(_MAX_HTML_BYTES + 1)
            if len(data) > _MAX_HTML_BYTES:
                raise ProbeError("Слишком большой ответ FunPay")
    except ProbeError:
        raise
    except error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            raise ProbeError("FunPay перенаправил запрос; проверка остановлена") from None
        if exc.code in (401, 403):
            raise ProbeError("FunPay отклонил авторизацию") from None
        if exc.code == 429:
            raise ProbeError("FunPay ограничил частоту запросов; повторите позже") from None
        raise ProbeError("FunPay вернул ошибку HTTP") from None
    except (error.URLError, TimeoutError, OSError, ssl.SSLError):
        raise ProbeError("Нет защищённого соединения с FunPay") from None
    try:
        parser = _ProfileHTML()
        parser.feed(data.decode("utf-8"))
        appdata = json.loads(html.unescape(parser.app_data or ""))
        uid = appdata.get("userId") if isinstance(appdata, dict) else None
        if not parser.username or type(uid) is not int or uid <= 0:
            raise ValueError("missing authorized profile")
        # Strip control characters so untrusted markup cannot format Telegram replies.
        username = "".join(c for c in parser.username if c.isprintable())[:60]
        if not username:
            raise ValueError("bad username")
        return "✅ FunPay: авторизация подтверждена (тест)\n👤 Аккаунт: " + username
    except (ValueError, TypeError, UnicodeError):
        raise ProbeError("Авторизация не подтверждена: изменился ответ сайта или ключ недействителен") from None


def status_from_environment(env=None, opener=None):
    """Never expose the key or raw upstream exception text."""
    env = os.environ if env is None else env
    key = env.get("FUNPAY_GOLDEN_KEY", "")
    if not key:
        return ("🔑 FunPay пока не подключён.\n"
                "Настройте Golden Key локально на VPS командой:\n"
                "sudo bash scripts/configure_funpay.sh\n"
                "Не отправляйте ключ в Telegram.")
    if opener is None and time.monotonic() < _CACHE["until"]:
        return _CACHE["response"]
    try:
        result = verify(key, opener=opener, user_agent=env.get("FUNPAY_USER_AGENT"))
    except ProbeError as exc:
        result = "⚠️ FunPay: " + str(exc)
    if opener is None:
        _CACHE["response"] = result  # cache contains no Golden Key
        _CACHE["until"] = time.monotonic() + 60
    return result
