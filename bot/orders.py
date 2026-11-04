"""Read-only, opt-in FunPay sales monitor.

Requests only the current seller's first /orders/trade page. No API writes,
chat messages, credential delivery or login-code processing. HTML may change.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from html.parser import HTMLParser
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib import request, error

from bot.funpay_probe import _KEY, _NoRedirect

ORDERS_URL = 'https://funpay.com/orders/trade'
_MAX_BYTES = 2 * 1024 * 1024
_ORDER_ID = re.compile(r'^[A-Za-z0-9_-]{4,64}$')
VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                  'link', 'meta', 'param', 'source', 'track', 'wbr'))


class OrderMonitorError(Exception):
    """Only safe diagnostic errors that never include a Golden Key."""


@dataclass(frozen=True)
class Order:
    order_id: str
    status: str  # paid, closed, refunded
    description: str = ''
    buyer: str = ''
    price: str = ''


def _clean(value, limit=140):
    return ' '.join(''.join(c for c in str(value) if c.isprintable()).split())[:limit]


class _SalesHTML(HTMLParser):
    """Minimal parser for FunPay /orders/trade tc-item cards; strict auth."""
    FIELDS = {'tc-order': 'id', 'order-desc': 'description',
              'tc-price': 'price', 'media-user-name': 'buyer'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.app_data = None
        self.authorized_header = False
        self.orders = []
        self.current = None
        self.depth = 0
        self.active_fields = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        if tag == 'body':
            self.app_data = attrs.get('data-app-data')
        if 'user-link-name' in classes:
            self.authorized_header = True
        if self.current is None and tag == 'a' and 'tc-item' in classes:
            self.current = {'id': [], 'description': [], 'price': [], 'buyer': [],
                            'status': 'refunded' if 'warning' in classes else
                                      'paid' if 'info' in classes else 'closed'}
            self.depth = 1
            self.active_fields = []
            return
        if self.current is not None:
            if tag not in VOID:
                self.depth += 1
            for css, field in self.FIELDS.items():
                if css in classes and tag in ('div', 'span'):
                    self.active_fields.append((field, self.depth))
                    break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.current is not None:
            for field, _ in self.active_fields:
                self.current[field].append(data)

    def handle_endtag(self, tag):
        if self.current is None or tag in VOID:
            return
        if self.depth == 1 and tag == 'a':
            fields = self.current
            self.current = None
            raw_id = _clean(''.join(fields['id']), 70).lstrip('#')
            if _ORDER_ID.fullmatch(raw_id) and len(self.orders) < 100:
                self.orders.append(Order(
                    raw_id, fields['status'], _clean(''.join(fields['description']), 200),
                    _clean(''.join(fields['buyer']), 80), _clean(''.join(fields['price']), 80)
                ))
            self.depth = 0
            self.active_fields = []
            return
        self.active_fields = [(field, depth) for field, depth in self.active_fields
                              if depth != self.depth]
        self.depth -= 1

    def validate(self):
        try:
            data = json.loads(html.unescape(self.app_data or ''))
            if (not self.authorized_header or not isinstance(data, dict) or
                    type(data.get('userId')) is not int or data['userId'] <= 0):
                raise ValueError('not signed in')
        except (ValueError, TypeError):
            raise OrderMonitorError('Авторизация FunPay не подтверждена; список продаж не прочитан') from None


def fetch_sales(golden_key, opener=None, timeout=12, user_agent=None):
    """Read-only HTTPS GET. No proxy environment, redirects or raw responses in errors."""
    if not isinstance(golden_key, str) or not _KEY.fullmatch(golden_key):
        raise OrderMonitorError('FunPay Golden Key не настроен')
    if user_agent is not None and (not isinstance(user_agent, str) or
                                   len(user_agent) > 300 or any(c in user_agent for c in '\r\n')):
        raise OrderMonitorError('Некорректный User-Agent')
    req = request.Request(ORDERS_URL, headers={
        'Cookie': 'golden_key=' + golden_key,
        'User-Agent': user_agent or 'Mozilla/5.0 FunPayRust/0.3.2b1 (read-only)',
        'Accept': 'text/html', 'Cache-Control': 'no-store',
    }, method='GET')
    client = opener if opener is not None else request.build_opener(request.ProxyHandler({}), _NoRedirect())
    try:
        with client.open(req, timeout=timeout) as resp:
            if resp.geturl() != ORDERS_URL:
                raise OrderMonitorError('Перенаправление FunPay; запрос остановлен')
            data = resp.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise OrderMonitorError('Ответ FunPay слишком большой')
    except OrderMonitorError:
        raise
    except error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            raise OrderMonitorError('Перенаправление FunPay; запрос остановлен') from None
        if exc.code in (401, 403):
            raise OrderMonitorError('FunPay отклонил авторизацию') from None
        if exc.code == 429:
            raise OrderMonitorError('FunPay ограничил частоту запросов') from None
        raise OrderMonitorError(f'FunPay HTTP {exc.code}') from None
    except (error.URLError, TimeoutError, OSError):
        raise OrderMonitorError('Нет защищённого соединения с FunPay') from None
    try:
        parser = _SalesHTML()
        parser.feed(data.decode('utf-8'))
        parser.validate()
        return parser.orders
    except (UnicodeError, ValueError) as exc:
        raise OrderMonitorError('Изменился HTML списка продаж FunPay') from None


class OrderStore:
    """Persistent, first-snapshot-silent order storage with safe limited fields."""

    def __init__(self, filename):
        self.filename = Path(filename)
        self.last_error = None  # in-process only; never stores upstream HTML
        try:
            if self.filename.is_symlink() or (self.filename.exists() and
                not self.filename.is_file()):
                raise OrderMonitorError('Небезопасный путь хранения заказов')
            self.filename.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS orders ('
                           'id TEXT PRIMARY KEY, status TEXT NOT NULL, '
                           'description TEXT NOT NULL, buyer TEXT NOT NULL, '
                           'price TEXT NOT NULL, updated_at INTEGER NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS metadata ('
                           'name TEXT PRIMARY KEY, value TEXT NOT NULL)')
                # Durable at-least-once Telegram delivery queue. Historical
                # events are queued atomically with the order snapshot.
                db.execute('CREATE TABLE IF NOT EXISTS delivery_outbox ('
                           'id INTEGER PRIMARY KEY AUTOINCREMENT, '
                           'recipient INTEGER NOT NULL, order_id TEXT NOT NULL, '
                           'kind TEXT NOT NULL, previous_status TEXT, '
                           'status TEXT NOT NULL, description TEXT NOT NULL, '
                           'buyer TEXT NOT NULL, price TEXT NOT NULL, '
                           'created_at INTEGER NOT NULL, delivered_at INTEGER)')
                db.execute('CREATE INDEX IF NOT EXISTS delivery_outbox_pending '
                           'ON delivery_outbox(delivered_at, id)')
        except (OSError, sqlite3.Error) as exc:
            raise OrderMonitorError('Не удалось открыть хранилище заказов') from None

    @contextmanager
    def _connect(self):
        if self.filename.is_symlink():
            raise OrderMonitorError('Небезопасный файл заказов')
        db = sqlite3.connect(str(self.filename), timeout=8)
        try:
            os.chmod(self.filename, 0o600)
            with db:
                yield db
        finally:
            db.close()

    def record(self, orders, recipients=()):
        """Store a snapshot and queue alert events in the SAME transaction.

        First successful snapshot is silent, as in v0.3.2. New paid sales and
        later closed/refunded transitions generate events. The return value
        remains a list of newly paid orders for existing callers.
        """
        if not isinstance(orders, list):
            raise OrderMonitorError('Некорректный список заказов')
        recipients = sorted({uid for uid in recipients if type(uid) is int and uid > 0})
        now = int(time.time())
        notifications = []
        try:
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                first = db.execute("SELECT value FROM metadata WHERE name='initialized'").fetchone() is None
                for order in orders[:100]:
                    if not isinstance(order, Order) or not _ORDER_ID.fullmatch(order.order_id):
                        continue
                    if order.status not in ('paid', 'closed', 'refunded'):
                        continue
                    previous = db.execute(
                        'SELECT status FROM orders WHERE id=?', (order.order_id,)
                    ).fetchone()
                    old_status = previous[0] if previous else None
                    kind = None
                    if not first:
                        if order.status == 'paid' and old_status != 'paid':
                            notifications.append(order)
                            kind = 'paid'
                        elif old_status is not None and old_status != order.status:
                            kind = 'status'
                    description = _clean(order.description, 200)
                    buyer = _clean(order.buyer, 80)
                    price = _clean(order.price, 80)
                    db.execute('INSERT INTO orders(id,status,description,buyer,price,updated_at) '
                               'VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                               'status=excluded.status, description=excluded.description, '
                               'buyer=excluded.buyer, price=excluded.price, updated_at=excluded.updated_at',
                               (order.order_id, order.status, description, buyer, price, now))
                    if kind:
                        for uid in recipients:
                            db.execute(
                                'INSERT INTO delivery_outbox(recipient,order_id,kind,'
                                'previous_status,status,description,buyer,price,created_at) '
                                'VALUES (?,?,?,?,?,?,?,?,?)',
                                (uid, order.order_id, kind, old_status,
                                 order.status, description, buyer, price, now))
                db.execute("INSERT OR REPLACE INTO metadata(name,value) VALUES('initialized','1')")
                db.execute("INSERT OR REPLACE INTO metadata(name,value) VALUES('last_poll',?)", (str(now),))
            self.last_error = None
            return notifications
        except (sqlite3.Error, OSError):
            raise OrderMonitorError('Не удалось сохранить результаты мониторинга') from None

    def pending_deliveries(self, limit=30):
        """Return (event_id, recipient, kind, previous_status, Order) tuples."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid delivery limit')
        try:
            with self._connect() as db:
                rows = db.execute(
                    'SELECT id,recipient,kind,previous_status,order_id,status,'
                    'description,buyer,price FROM delivery_outbox '
                    'WHERE delivered_at IS NULL ORDER BY id ASC LIMIT ?', (limit,)
                ).fetchall()
            return [(r[0], r[1], r[2], r[3], Order(*r[4:])) for r in rows]
        except (sqlite3.Error, OSError):
            raise OrderMonitorError('Не удалось прочитать очередь уведомлений') from None

    def mark_delivered(self, event_id):
        try:
            with self._connect() as db:
                db.execute('UPDATE delivery_outbox SET delivered_at=? '
                           'WHERE id=? AND delivered_at IS NULL',
                           (int(time.time()), event_id))
        except (sqlite3.Error, OSError):
            raise OrderMonitorError('Не удалось отметить доставку уведомления') from None

    def find(self, query, limit=7):
        """Search only saved orders, using bound parameters (no wildcard injection)."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 80:
            return []
        q = query.strip()
        try:
            with self._connect() as db:
                rows = db.execute(
                    'SELECT id,status,description,buyer,price FROM orders '
                    'WHERE instr(lower(id),lower(?))>0 OR '
                    'instr(lower(buyer),lower(?))>0 OR '
                    'instr(lower(description),lower(?))>0 '
                    'ORDER BY updated_at DESC, id DESC LIMIT ?',
                    (q, q, q, min(7, max(1, limit)))
                ).fetchall()
            return [Order(*r) for r in rows]
        except (sqlite3.Error, OSError):
            raise OrderMonitorError('Не удалось выполнить поиск заказов') from None

    def get_order(self, order_id):
        if type(order_id) is not str or not _ORDER_ID.fullmatch(order_id):
            return None
        try:
            with self._connect() as db:
                row = db.execute(
                    'SELECT id,status,description,buyer,price FROM orders WHERE id=?',
                    (order_id,)
                ).fetchone()
            return Order(*row) if row else None
        except (sqlite3.Error, OSError):
            raise OrderMonitorError('Не удалось прочитать заказ') from None

    def summary(self):
        try:
            with self._connect() as db:
                count = db.execute('SELECT count(*) FROM orders').fetchone()[0]
                last = db.execute("SELECT value FROM metadata WHERE name='last_poll'").fetchone()
                rows = db.execute('SELECT id,status,description,buyer,price FROM orders '
                                  'ORDER BY updated_at DESC, id DESC LIMIT 7').fetchall()
            return count, int(last[0]) if last else None, [Order(*r) for r in rows]
        except (sqlite3.Error, OSError):
            raise OrderMonitorError('Не удалось прочитать историю заказов') from None


def order_url(order_id):
    """Return a FunPay order permalink for a strictly validated order ID.

    Order IDs come from untrusted HTML. Never echo the site's href attribute
    or build an arbitrary redirect from it. No credentials appear in the URL.
    """
    if type(order_id) is not str or not _ORDER_ID.fullmatch(order_id):
        return None
    return 'https://funpay.com/orders/' + order_id + '/'


def order_button(order_id):
    """One safe Telegram URL button, or None for malformed order IDs."""
    url = order_url(order_id)
    if url is None:
        return None
    return {'text': '🔗 Открыть заказ #' + order_id[:32], 'url': url}


def describe_order(order):
    status = {'paid': '💰 Оплачен', 'closed': '✅ Закрыт',
              'refunded': '↩️ Возврат'}.get(order.status, '❔')
    return (f'📦 Заказ #{order.order_id}\n{status}\n'
            f'Товар: {order.description or "не указан"}\n'
            f'Покупатель: {order.buyer or "не указан"}\n'
            f'Цена: {order.price or "не указана"}')
