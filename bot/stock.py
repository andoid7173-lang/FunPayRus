"""Local inventory for manually verified digital goods.

No FunPay writes, automatic fulfillment or secret-bearing Telegram messages.
Items remain assigned once reserved; there is intentionally NO release method.
"""
import os
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager

SKU_RE = re.compile(r'^[A-Z0-9][A-Z0-9_-]{1,39}$')
ORDER_RE = re.compile(r'^[A-Za-z0-9_-]{4,64}$')


class StockError(Exception):
    """Redacted storage error; never contains inventory values."""


class StockStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink() or not self.path.is_file():
            raise StockError('Сначала создайте хранилище заказов через установку бота')
        try:
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS stock_items ('
                           'id INTEGER PRIMARY KEY AUTOINCREMENT,'
                           'sku TEXT NOT NULL, value TEXT NOT NULL UNIQUE,'
                           'order_id TEXT UNIQUE,'
                           "state TEXT NOT NULL DEFAULT 'available' "
                           "CHECK(state IN ('available','reserved','issued','void')),"
                           'created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS stock_by_sku '
                           'ON stock_items(sku,state,id)')
        except (sqlite3.Error, OSError):
            raise StockError('Не удалось подготовить локальный склад') from None

    @contextmanager
    def connect(self):
        if self.path.is_symlink() or not self.path.is_file():
            raise StockError('Небезопасный путь к складу')
        db = None
        try:
            db = sqlite3.connect(str(self.path), timeout=8)
            os.chmod(self.path, 0o600)
            with db:
                yield db
        except sqlite3.IntegrityError:
            raise
        except sqlite3.Error:
            raise StockError('Ошибка локального склада') from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def valid_sku(sku):
        return type(sku) is str and bool(SKU_RE.fullmatch(sku))

    @staticmethod
    def valid_order(order_id):
        return type(order_id) is str and bool(ORDER_RE.fullmatch(order_id))

    def add(self, sku, value):
        """Add an opaque digital code only via the local CLI (never Telegram)."""
        if (not self.valid_sku(sku) or type(value) is not str or
                not 1 <= len(value) <= 512 or any(
                    ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            return 'invalid'
        try:
            with self.connect() as db:
                now = int(time.time())
                db.execute('INSERT INTO stock_items('
                           'sku,value,state,created_at,updated_at) '
                           "VALUES (?,?,'available',?,?)", (sku, value, now, now))
            return 'added'
        except StockError:
            raise
        except sqlite3.IntegrityError:
            return 'duplicate'

    def summary(self):
        """SKU counts only: never fetch secret values for Telegram."""
        with self.connect() as db:
            rows = db.execute(
                'SELECT sku,state,count(*) FROM stock_items '
                'GROUP BY sku,state ORDER BY sku,state'
            ).fetchall()
        result = {}
        for sku, state, count in rows:
            result.setdefault(sku, {'available': 0, 'reserved': 0,
                                    'issued': 0, 'void': 0})[state] = count
        return result

    def reservation(self, order_id):
        """Nonsecret state for an order."""
        if not self.valid_order(order_id):
            return None
        with self.connect() as db:
            row = db.execute('SELECT sku,state FROM stock_items WHERE order_id=?',
                             (order_id,)).fetchone()
        return tuple(row) if row else None

    def reserve(self, order_id, sku):
        """Atomically bind one available code to a candidate order.

        Requires both paid HTML status and a pending review task; a human MUST
        independently confirm payment on the FunPay site before invoking this.
        No code leaves the server from this method.
        """
        if not self.valid_order(order_id) or not self.valid_sku(sku):
            return 'invalid'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute(
                'SELECT sku,state FROM stock_items WHERE order_id=?',
                (order_id,)
            ).fetchone()
            if existing:
                return 'already' if existing[0] == sku else 'conflict'
            candidate = db.execute(
                'SELECT q.state,o.status FROM review_queue AS q '
                'JOIN orders AS o ON o.id=q.order_id WHERE q.order_id=?',
                (order_id,)
            ).fetchone()
            if candidate is None:
                return 'missing'
            if candidate != ('pending', 'paid'):
                return 'blocked'
            item = db.execute(
                "SELECT id FROM stock_items WHERE sku=? AND state='available' "
                'ORDER BY id ASC LIMIT 1', (sku,)
            ).fetchone()
            if item is None:
                return 'empty'
            db.execute(
                "UPDATE stock_items SET state='reserved',order_id=?,updated_at=? "
                "WHERE id=? AND state='available'",
                (order_id, int(time.time()), item[0])
            )
        return 'reserved'

    def secret_for_local_terminal(self, order_id):
        """Never call from a bot handler; caller must enforce local TTY."""
        if not self.valid_order(order_id):
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM stock_items WHERE order_id=? "
                "AND state='reserved'", (order_id,)
            ).fetchone()
        return row[0] if row else None

    def complete(self, order_id, owner_id):
        """Record manual delivery and close review in the same transaction."""
        if (not self.valid_order(order_id) or
                type(owner_id) is not int or owner_id <= 0):
            return 'invalid'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stock = db.execute(
                'SELECT state FROM stock_items WHERE order_id=?',
                (order_id,)
            ).fetchone()
            if stock is None:
                return 'missing'
            if stock[0] == 'issued':
                return 'already'
            if stock[0] != 'reserved':
                return 'blocked'
            candidate = db.execute(
                'SELECT q.state,o.status FROM review_queue AS q '
                'JOIN orders AS o ON o.id=q.order_id WHERE q.order_id=?',
                (order_id,)
            ).fetchone()
            if candidate != ('pending', 'paid'):
                return 'blocked'
            now = int(time.time())
            db.execute(
                "UPDATE stock_items SET state='issued', updated_at=? "
                "WHERE order_id=? AND state='reserved'",
                (now, order_id)
            )
            db.execute(
                "UPDATE review_queue SET state='done',reviewed_by=?,updated_at=? "
                "WHERE order_id=? AND state='pending'",
                (owner_id, now, order_id)
            )
        return 'done'

    def void(self, order_id):
        """Permanently retire a reserved code, even if order was refunded."""
        if not self.valid_order(order_id):
            return 'invalid'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stock = db.execute(
                'SELECT state FROM stock_items WHERE order_id=?',
                (order_id,)
            ).fetchone()
            if stock is None:
                return 'missing'
            if stock[0] == 'void':
                return 'already'
            if stock[0] != 'reserved':
                return 'blocked'
            db.execute(
                "UPDATE stock_items SET state='void',updated_at=? WHERE "
                "order_id=? AND state='reserved'",
                (int(time.time()), order_id)
            )
        return 'void'
