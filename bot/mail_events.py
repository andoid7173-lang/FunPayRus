"""Privacy-first, provider-neutral mailbox event ledger.

This module NEVER authenticates to mail services, reads message bodies,
extracts login codes, forwards tokens, or writes to FunPay/Steam.
Only a separately audited, user-authorized metadata adapter may call ingest().
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager

_PLATFORM = re.compile(r'^[a-z][a-z0-9_-]{1,31}$')
_PROVIDER = re.compile(r'^[a-z][a-z0-9_-]{1,31}$')
_KINDS = frozenset(('account_notice', 'security_notice', 'purchase_notice',
                    'delivery_notice', 'other'))
# Use explicit classifiers in adapters. Never trust a sender's From header as
# proof of origin. Not even 'security_notice' proves a legitimate Steam login.
_MAX_EVENTS = 25000


class MailEventError(Exception):
    """Safe diagnostics that never quote mailbox metadata."""


class MailEventStore:
    def __init__(self, filename, *, hash_key=None):
        self.filename = Path(filename)
        if self.filename.is_symlink() or not self.filename.is_file():
            raise MailEventError('Хранилище недоступно')
        if hash_key is not None and (type(hash_key) is not bytes or len(hash_key) < 32):
            raise ValueError('A separate 32-byte key is required')
        self.hash_key = hash_key
        try:
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS mail_events ('
                           'fingerprint TEXT PRIMARY KEY, '
                           'provider TEXT NOT NULL, platform TEXT NOT NULL, '
                           'kind TEXT NOT NULL, received_at INTEGER NOT NULL, '
                           'created_at INTEGER NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS mail_events_created '
                           'ON mail_events(created_at DESC)')
        except (OSError, sqlite3.Error):
            raise MailEventError('Не удалось создать журнал событий почты') from None

    @contextmanager
    def connect(self):
        if self.filename.is_symlink() or not self.filename.is_file():
            raise MailEventError('Небезопасное хранилище')
        db = None
        try:
            db = sqlite3.connect(str(self.filename), timeout=8)
            os.chmod(self.filename, 0o600)
            with db:
                yield db
        except (OSError, sqlite3.Error):
            raise MailEventError('Ошибка журнала почтовых событий') from None
        finally:
            if db is not None:
                db.close()

    def ingest(self, *, provider, platform, kind, opaque_id, received_at):
        """Deduplicate only sanitized, non-sensitive metadata.

        No subject, sender, email address, account name, code or body accepted.
        An adapter must supply an opaque provider message ID and trusted timestamp.
        The opaque ID is *never* saved verbatim. The value returned is a status
        suitable for a private Telegram administrative summary.
        """
        if (type(provider) is not str or not _PROVIDER.fullmatch(provider) or
                type(platform) is not str or not _PLATFORM.fullmatch(platform) or
                kind not in _KINDS or type(opaque_id) is not str or
                not 1 <= len(opaque_id) <= 1024 or
                type(received_at) is not int):
            return 'invalid'
        now = int(time.time())
        # Ignore stale/future untrusted events (no alert flood at first sync).
        if received_at < now - 86400 or received_at > now + 300:
            return 'stale'
        if self.hash_key is None:
            # Privacy-preserving dedup needs an installation-specific key.
            return 'not_configured'
        raw = '\x00'.join((provider, platform, opaque_id)).encode('utf-8')
        fingerprint = hmac.new(self.hash_key, raw, hashlib.sha256).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT 1 FROM mail_events WHERE fingerprint=?',
                                  (fingerprint,)).fetchone()
            if existing:
                return 'duplicate'
            db.execute(
                'INSERT INTO mail_events('
                'fingerprint,provider,platform,kind,received_at,created_at) '
                'VALUES (?,?,?,?,?,?)',
                (fingerprint, provider, platform, kind, received_at, now)
            )
            db.execute(
                'DELETE FROM mail_events WHERE fingerprint IN ('
                'SELECT fingerprint FROM mail_events '
                'ORDER BY created_at DESC,fingerprint DESC LIMIT -1 OFFSET ?)',
                (_MAX_EVENTS,)
            )
        return 'recorded'

    def summary(self, limit=12):
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError('Invalid limit')
        with self.connect() as db:
            counts = db.execute(
                'SELECT provider,platform,kind,count(*) FROM mail_events '
                'GROUP BY provider,platform,kind '
                'ORDER BY count(*) DESC,platform ASC LIMIT ?', (limit,)
            ).fetchall()
            total = db.execute('SELECT count(*) FROM mail_events').fetchone()[0]
        return total, counts
