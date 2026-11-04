"""Local, owner-controlled admin roles and notification preferences.

Settings are written atomically in /var/lib/funpayrus; secrets are never stored
here. A damaged settings file fails closed instead of granting default access.
"""
import json
import os
from pathlib import Path
import tempfile


class SettingsError(Exception):
    """Invalid or unsafe local settings."""


class Settings:
    MAX_ADMINS = 5

    def __init__(self, owner_id: int, filename: Path):
        if type(owner_id) is not int or owner_id <= 0:
            raise ValueError('Invalid owner id')
        self.owner_id = owner_id
        self.filename = Path(filename)
        self._admins = {}
        self._startup_alerts = set()
        self._order_alerts = set()
        self._order_monitor = False
        self._read()

    def _read(self):
        try:
            if self.filename.is_symlink():
                raise SettingsError('Файл настроек является ссылкой')
            if not self.filename.exists():
                return
            if not self.filename.is_file() or self.filename.stat().st_size > 16384:
                raise SettingsError('Некорректный файл настроек')
            raw = json.loads(self.filename.read_text(encoding='utf-8'))
            if (not isinstance(raw, dict) or not {'admins', 'startup_alerts'} <= set(raw) or
                    not set(raw) <= {'admins', 'startup_alerts', 'order_alerts', 'order_monitor'}):
                raise SettingsError('Некорректная структура настроек')
            admins = raw['admins']
            alerts = raw['startup_alerts']
            order_alerts = raw.get('order_alerts', [])
            order_monitor = raw.get('order_monitor', False)
            if type(order_monitor) is not bool:
                raise SettingsError('Некорректный режим мониторинга')
            if not isinstance(admins, dict) or len(admins) > self.MAX_ADMINS:
                raise SettingsError('Некорректные администраторы')
            parsed = {}
            for key, role in admins.items():
                if (not isinstance(key, str) or not key.isascii() or
                    not key.isdecimal() or int(key) <= 0 or str(int(key)) != key or
                    int(key) == self.owner_id or role not in ('admin', 'viewer')):
                    raise SettingsError('Некорректная роль')
                parsed[int(key)] = role
            if not isinstance(alerts, list) or len(alerts) > self.MAX_ADMINS + 1:
                raise SettingsError('Некорректные уведомления')
            parsed_alerts = set()
            for uid in alerts:
                if type(uid) is not int or uid <= 0 or uid not in parsed and uid != self.owner_id:
                    raise SettingsError('Некорректный получатель уведомлений')
                parsed_alerts.add(uid)
            if not isinstance(order_alerts, list) or len(order_alerts) > self.MAX_ADMINS + 1:
                raise SettingsError('Некорректные уведомления о заказах')
            parsed_order_alerts = set()
            for uid in order_alerts:
                if type(uid) is not int or uid <= 0 or uid not in parsed and uid != self.owner_id:
                    raise SettingsError('Некорректный получатель заказов')
                parsed_order_alerts.add(uid)
            self._admins = parsed
            self._startup_alerts = parsed_alerts
            self._order_alerts = parsed_order_alerts
            self._order_monitor = order_monitor
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise SettingsError('Не удалось безопасно прочитать настройки') from None

    def role(self, user_id):
        if type(user_id) is not int:
            return None
        if user_id == self.owner_id:
            return 'owner'
        return self._admins.get(user_id)

    def list_admins(self):
        return [(self.owner_id, 'owner')] + sorted(self._admins.items())

    def startup_enabled(self, user_id):
        return user_id in self._startup_alerts and self.role(user_id) is not None

    def alert_recipients(self):
        return sorted(self._startup_alerts)

    def order_alert_enabled(self, user_id):
        return user_id in self._order_alerts and self.role(user_id) in ('owner', 'admin')

    def order_alert_recipients(self):
        return sorted(uid for uid in self._order_alerts if self.role(uid) in ('owner', 'admin'))

    def monitor_enabled(self):
        return self._order_monitor

    def set_monitor(self, enabled):
        if type(enabled) is not bool:
            return False
        self._save(dict(self._admins), set(self._startup_alerts), monitor=enabled)
        return True

    def toggle_order_alert(self, user_id):
        if self.role(user_id) not in ('owner', 'admin'):
            return False
        alerts = set(self._order_alerts)
        if user_id in alerts:
            alerts.remove(user_id)
        else:
            alerts.add(user_id)
        self._save(dict(self._admins), set(self._startup_alerts), order_alerts=alerts)
        return True

    def _save(self, admins, alerts, order_alerts=None, monitor=None):
        if order_alerts is None:
            order_alerts = set(self._order_alerts)
        if monitor is None:
            monitor = self._order_monitor
        tmp = None
        try:
            if self.filename.is_symlink():
                raise OSError('unsafe symlink')
            parent = self.filename.parent
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = {'admins': {str(uid): role for uid, role in sorted(admins.items())},
                    'startup_alerts': sorted(alerts),
                    'order_alerts': sorted(order_alerts),
                    'order_monitor': monitor}
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=parent, prefix='.settings-', delete=False) as fp:
                tmp = Path(fp.name)
                os.chmod(fp.name, 0o600)
                json.dump(data, fp, ensure_ascii=False)
                fp.write('\n')
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, self.filename)
            fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise SettingsError('Не удалось сохранить настройки') from None
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        self._admins = admins
        self._startup_alerts = alerts
        self._order_alerts = order_alerts
        self._order_monitor = monitor

    def add(self, user_id, role='viewer'):
        if (type(user_id) is not int or user_id <= 0 or user_id == self.owner_id or
            role not in ('viewer', 'admin')):
            return False
        if user_id not in self._admins and len(self._admins) >= self.MAX_ADMINS:
            return False
        new = dict(self._admins)
        new[user_id] = role
        self._save(new, set(self._startup_alerts))
        return True

    def remove(self, user_id):
        if user_id not in self._admins:
            return False
        new = dict(self._admins)
        del new[user_id]
        self._save(new, self._startup_alerts - {user_id},
                   order_alerts=self._order_alerts - {user_id})
        return True

    def toggle_startup(self, user_id):
        if self.role(user_id) is None:
            return False
        alerts = set(self._startup_alerts)
        if user_id in alerts:
            alerts.remove(user_id)
        else:
            alerts.add(user_id)
        self._save(dict(self._admins), alerts)
        return True
