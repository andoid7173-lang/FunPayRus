"""Owner-only inbox notices: durable local outbox and safe bounded polling.

Never receives or stores bodies, subjects, attachments, email addresses or OTPs.
At-least-once Telegram notification semantics: crashes may repeat an alert,
but successful alerts are not dropped by a network error.
"""
import base64
import hashlib
import hmac
from pathlib import Path
import sqlite3
import time

from bot.mail_auth import KEY_FILE, MailAuthError, access_token
from bot.mail_outlook import GraphError, PLATFORMS, fetch_since

POLL_SECONDS = 120
OVERLAP_SECONDS = 180
ALERT_TEMPLATE = "Новое уведомление: {name} (Outlook). Проверь его самостоятельно в официальном приложении. Отправитель письма не подтверждён."
PLATFORM_NAMES = {
    "steam": "Steam", "epic": "Epic Games", "ea": "EA",
    "ubisoft": "Ubisoft", "battlenet": "Battle.net", "gog": "GOG"
}


class MailServiceError(Exception):
    pass


class MailService:
    def __init__(self, dbfile, *, key_file=KEY_FILE, clock=time.time):
        self.filename = Path(dbfile)
        self.clock = clock
        key_path = Path(key_file)
        if (self.filename.is_symlink() or not self.filename.is_file()
                or key_path.is_symlink() or not key_path.is_file()
                or key_path.stat().st_mode & 0o007):
            raise MailServiceError("Настройки безопасности почты отсутствуют")
        try:
            raw = base64.urlsafe_b64decode(key_path.read_bytes())
            if len(raw) != 32:
                raise ValueError("key size")
            self.hmac_key = hmac.new(raw, b"funpayrus-mail-id-v1", hashlib.sha256).digest()
            with self.connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS mail_monitor_state ("
                           "name TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS mail_monitor_outbox ("
                           "fingerprint TEXT PRIMARY KEY, platform TEXT NOT NULL, "
                           "received_at INTEGER NOT NULL, sent_at INTEGER)")
                db.execute("CREATE INDEX IF NOT EXISTS mail_monitor_pending "
                           "ON mail_monitor_outbox(sent_at,received_at)")
        except (OSError, ValueError, sqlite3.Error):
            raise MailServiceError("Не удалось открыть защищённый журнал почты") from None

    def connect(self):
        if self.filename.is_symlink() or not self.filename.is_file():
            raise MailServiceError("Недоступна защищённая база")
        return sqlite3.connect(str(self.filename), timeout=8)

    def is_enabled(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM mail_monitor_state WHERE name='enabled'"
            ).fetchone()
            return bool(row and row[0] == "1")

    def enable(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            was_enabled = db.execute(
                "SELECT value FROM mail_monitor_state WHERE name='enabled'"
            ).fetchone()
            if not was_enabled or was_enabled[0] != "1":
                # Reconnection must NOT replay messages received while disabled.
                db.execute("DELETE FROM mail_monitor_state WHERE name IN ('since','floor')")
                db.execute("DELETE FROM mail_monitor_outbox WHERE sent_at IS NULL")
            db.execute("INSERT OR REPLACE INTO mail_monitor_state(name,value) "
                       "VALUES ('enabled','1')")

    def disable(self):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO mail_monitor_state(name,value) "
                       "VALUES ('enabled','0')")
            db.execute("DELETE FROM mail_monitor_outbox WHERE sent_at IS NULL")

    def reset_cursor(self):
        with self.connect() as db:
            db.execute("DELETE FROM mail_monitor_state WHERE name='since'")
            db.execute("DELETE FROM mail_monitor_outbox WHERE sent_at IS NULL")

    def fingerprint(self, message_id):
        return hmac.new(self.hmac_key, message_id.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def status(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM mail_monitor_state WHERE name='since'"
            ).fetchone()
            count = db.execute(
                "SELECT COUNT(*) FROM mail_monitor_outbox WHERE sent_at IS NULL"
            ).fetchone()[0]
        return {"enabled": self.is_enabled(), "initialized": row is not None,
                "pending": count}

    def poll(self, client_id, *, fetcher=fetch_since):
        """Fetch full interval before checkpoint advance; no network when disabled."""
        if not self.is_enabled():
            return "disabled"
        now = int(self.clock())
        with self.connect() as db:
            row = db.execute("SELECT value FROM mail_monitor_state WHERE name='since'").fetchone()
        if row is None:
            # A first sync starts *now*; no historical email is sent.
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT OR IGNORE INTO mail_monitor_state(name,value) "
                           "VALUES('since',?)", (str(now),))
                db.execute("INSERT OR IGNORE INTO mail_monitor_state(name,value) "
                           "VALUES('floor',?)", (str(now),))
            return "initialized"
        previous = int(row[0])
        with self.connect() as db:
            floor_row = db.execute("SELECT value FROM mail_monitor_state WHERE name='floor'").fetchone()
        floor = int(floor_row[0]) if floor_row else previous
        if previous > now + 300:
            raise MailServiceError("Неверное время сервера")
        token = access_token(client_id)
        if not token:
            return "not-connected"
        try:
            entries = fetcher(token, max(0, previous - OVERLAP_SECONDS))
        except GraphError:
            raise MailServiceError("Microsoft Graph: проверка не выполнена") from None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for message_id, received_at, platform in entries:
                if platform not in PLATFORMS or received_at <= floor or received_at > now + 300:
                    continue
                digest = self.fingerprint(message_id)
                db.execute("INSERT OR IGNORE INTO mail_monitor_outbox("
                           "fingerprint,platform,received_at,sent_at) "
                           "VALUES(?,?,?,NULL)", (digest, platform, received_at))
            # No missed page: fetcher raises before committing when truncated.
            db.execute("UPDATE mail_monitor_state SET value=? WHERE name='since'",
                       (str(max(previous, now)),))
            # Retain 30 days for dedup. Pending alerts remain for retry.
            db.execute("DELETE FROM mail_monitor_outbox "
                       "WHERE sent_at IS NOT NULL AND received_at < ?",
                       (now - 30 * 86400,))
        return "ok"

    def flush(self, send_owner, limit=20):
        if not self.is_enabled():
            return 0
        with self.connect() as db:
            rows = db.execute(
                "SELECT fingerprint,platform FROM mail_monitor_outbox "
                "WHERE sent_at IS NULL ORDER BY received_at,fingerprint LIMIT ?",
                (min(limit, 30),)
            ).fetchall()
        delivered = 0
        for fingerprint, platform in rows:
            message = ALERT_TEMPLATE.format(name=PLATFORM_NAMES.get(platform, "Платформа"))
            # Do not treat the untrusted From header as proof of authenticity.
            send_owner(message)
            with self.connect() as db:
                db.execute("UPDATE mail_monitor_outbox SET sent_at=? "
                           "WHERE fingerprint=? AND sent_at IS NULL",
                           (int(self.clock()), fingerprint))
            delivered += 1
        return delivered
