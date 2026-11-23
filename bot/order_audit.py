"""Explicit manual order verification and append-only, nonsecret audit history.

Only the OWNER's Telegram command is allowed to invoke verify(); this class
additionally checks positive numeric actor IDs and the local order status.
A verification is an operator assertion, NOT external proof of payment.
"""
from pathlib import Path
import os
import re
import sqlite3
import time
from contextlib import contextmanager

ORDER_ID = re.compile(r'^[A-Za-z0-9_-]{4,64}$')
ACTIONS = frozenset((
    'verified', 'verification_revoked', 'reserved', 'issued', 'void', 'manual_done'
))


class AuditError(Exception):
    """Never includes order details or digital goods."""


def create_schema(db):
    """Idempotent migration; can be safely called for existing SQLite stores."""
    db.execute(
        'CREATE TABLE IF NOT EXISTS manual_verifications ('
        'order_id TEXT PRIMARY KEY, verified_by INTEGER NOT NULL, '
        'verified_at INTEGER NOT NULL)'
    )
    db.execute(
        'CREATE TABLE IF NOT EXISTS order_action_audit ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'order_id TEXT NOT NULL, action TEXT NOT NULL, '
        'actor INTEGER, created_at INTEGER NOT NULL)'
    )
    db.execute(
        'CREATE INDEX IF NOT EXISTS order_action_audit_order '
        'ON order_action_audit(order_id,id)'
    )


def append_action(db, order_id, action, actor=None):
    if (type(order_id) is not str or not ORDER_ID.fullmatch(order_id) or
            action not in ACTIONS or
            (actor is not None and (type(actor) is not int or actor <= 0))):
        raise ValueError('Invalid audit record')
    db.execute(
        'INSERT INTO order_action_audit(order_id,action,actor,created_at) '
        'VALUES (?,?,?,?)', (order_id, action, actor, int(time.time()))
    )


class AuditStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink() or not self.path.is_file():
            raise AuditError('Небезопасный файл базы заказов')
        try:
            with self.connect() as db:
                create_schema(db)
        except (OSError, sqlite3.Error):
            raise AuditError('Не удалось подготовить журнал действий') from None

    @contextmanager
    def connect(self):
        if self.path.is_symlink() or not self.path.is_file():
            raise AuditError('Небезопасная база заказов')
        try:
            db = sqlite3.connect(str(self.path), timeout=8)
            os.chmod(self.path, 0o600)
        except (OSError, sqlite3.Error):
            raise AuditError('База заказов недоступна') from None
        try:
            with db:
                yield db
        except sqlite3.Error:
            raise AuditError('Ошибка журнала операций') from None
        finally:
            db.close()

    def verify(self, order_id, owner_id):
        """Record owner's check on official FunPay page; no network or delivery."""
        if (type(order_id) is not str or not ORDER_ID.fullmatch(order_id) or
                type(owner_id) is not int or owner_id <= 0):
            return 'invalid'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                'SELECT q.state,o.status FROM review_queue AS q '
                'JOIN orders AS o ON o.id=q.order_id WHERE q.order_id=?',
                (order_id,)
            ).fetchone()
            if row is None:
                return 'missing'
            if row != ('pending', 'paid'):
                return 'blocked'
            existing = db.execute(
                'SELECT 1 FROM manual_verifications WHERE order_id=?',
                (order_id,)
            ).fetchone()
            if existing:
                return 'already'
            db.execute(
                'INSERT INTO manual_verifications(order_id,verified_by,verified_at) '
                'VALUES (?,?,?)', (order_id, owner_id, int(time.time()))
            )
            append_action(db, order_id, 'verified', owner_id)
        return 'verified'

    def history(self, order_id, limit=15):
        """Recent action names only, never stock values or customer details."""
        if type(order_id) is not str or not ORDER_ID.fullmatch(order_id):
            return []
        if type(limit) is not int or not 1 <= limit <= 40:
            raise ValueError('Invalid audit limit')
        with self.connect() as db:
            rows = db.execute(
                'SELECT action,actor,created_at FROM order_action_audit '
                'WHERE order_id=? ORDER BY id DESC LIMIT ?', (order_id, limit)
            ).fetchall()
        return rows
