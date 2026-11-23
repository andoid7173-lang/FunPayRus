"""Local operator tools: private notes, inventory thresholds, truthful snapshot stats.

All values are stored in the same SQLite file as orders and inventory.
No external requests, no credentials, no customer messages.
"""
import os
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager

_ORDER_ID = re.compile(r'^[A-Za-z0-9_-]{4,64}$')
_SKU = re.compile(r'^[A-Z0-9][A-Z0-9_-]{1,39}$')


class OperatorError(Exception):
    """Diagnostic deliberately omits database contents."""


class OperatorStore:
    def __init__(self, filename):
        self.filename = Path(filename)
        if self.filename.is_symlink() or not self.filename.is_file():
            raise OperatorError('Хранилище не найдено или путь небезопасен')
        try:
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS operator_notes ('
                           'order_id TEXT PRIMARY KEY, note TEXT NOT NULL, '
                           'editor INTEGER NOT NULL, updated_at INTEGER NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS stock_thresholds ('
                           'sku TEXT PRIMARY KEY, min_available INTEGER NOT NULL '
                           'CHECK(min_available BETWEEN 0 AND 10000))')
        except (OSError, sqlite3.Error):
            raise OperatorError('Не удалось подготовить инструменты оператора') from None

    @contextmanager
    def connect(self):
        if self.filename.is_symlink() or not self.filename.is_file():
            raise OperatorError('Небезопасный путь хранилища')
        db = None
        try:
            db = sqlite3.connect(str(self.filename), timeout=8)
            os.chmod(self.filename, 0o600)
            with db:
                yield db
        except (OSError, sqlite3.Error):
            raise OperatorError('Ошибка доступа к инструментам оператора') from None
        finally:
            if db is not None:
                db.close()

    def note(self, order_id):
        if type(order_id) is not str or not _ORDER_ID.fullmatch(order_id):
            return None
        with self.connect() as db:
            row = db.execute('SELECT note FROM operator_notes WHERE order_id=?',
                             (order_id,)).fetchone()
        return row[0] if row else None

    def set_note(self, order_id, value, editor):
        if (type(order_id) is not str or not _ORDER_ID.fullmatch(order_id) or
                type(value) is not str or type(editor) is not int or editor <= 0):
            return 'invalid'
        value = value.strip()
        if not 1 <= len(value) <= 500 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            return 'invalid'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute('SELECT 1 FROM orders WHERE id=?', (order_id,)).fetchone()
            if exists is None:
                return 'missing'
            db.execute('INSERT INTO operator_notes(order_id,note,editor,updated_at) '
                       'VALUES (?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET '
                       'note=excluded.note,editor=excluded.editor,'
                       'updated_at=excluded.updated_at',
                       (order_id, value, editor, int(time.time())))
        return 'saved'

    def clear_note(self, order_id):
        if type(order_id) is not str or not _ORDER_ID.fullmatch(order_id):
            return 'invalid'
        with self.connect() as db:
            cursor = db.execute('DELETE FROM operator_notes WHERE order_id=?',
                                (order_id,))
            return 'cleared' if cursor.rowcount else 'missing'

    def set_threshold(self, sku, value):
        if (type(sku) is not str or not _SKU.fullmatch(sku) or
                type(value) is not int or not 0 <= value <= 10000):
            return 'invalid'
        with self.connect() as db:
            exists = db.execute('SELECT 1 FROM stock_items WHERE sku=? LIMIT 1',
                                (sku,)).fetchone()
            if exists is None:
                return 'missing'
            db.execute('INSERT INTO stock_thresholds(sku,min_available) VALUES (?,?) '
                       'ON CONFLICT(sku) DO UPDATE SET '
                       'min_available=excluded.min_available', (sku, value))
        return 'saved'

    def low_stock(self, default_threshold=3):
        if type(default_threshold) is not int or not 0 <= default_threshold <= 10000:
            raise ValueError('Invalid threshold')
        with self.connect() as db:
            rows = db.execute(
                'SELECT s.sku, '
                "SUM(CASE WHEN s.state='available' THEN 1 ELSE 0 END), "
                'COALESCE(t.min_available, ?) '
                'FROM stock_items AS s '
                'LEFT JOIN stock_thresholds AS t ON t.sku=s.sku '
                'GROUP BY s.sku ORDER BY s.sku', (default_threshold,)
            ).fetchall()
        return [(sku, available, threshold) for sku, available, threshold in rows
                if available <= threshold]

    def stats(self):
        """Observed snapshots, not a sales, income or payment report."""
        with self.connect() as db:
            statuses = dict(db.execute('SELECT status, count(*) FROM orders '
                                       'GROUP BY status').fetchall())
            review = dict(db.execute('SELECT state, count(*) FROM review_queue '
                                     'GROUP BY state').fetchall())
            stock = dict(db.execute('SELECT state, count(*) FROM stock_items '
                                    'GROUP BY state').fetchall())
            pending = db.execute('SELECT count(*) FROM delivery_outbox '
                                 'WHERE delivered_at IS NULL').fetchone()[0]
            note_count = db.execute('SELECT count(*) FROM operator_notes').fetchone()[0]
            last = db.execute("SELECT value FROM metadata WHERE name='last_poll'").fetchone()
        return {
            'orders': statuses, 'reviews': review, 'stock': stock,
            'undelivered_alerts': pending, 'notes': note_count,
            'last_poll': int(last[0]) if last else None,
        }
