"""Run Telegram admin bot with private role-aware dispatch and long polling."""
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import signal
import threading
import time

from bot.checkpoint import CheckpointError, UpdateCheckpoint
from bot.commands import reply_for, callback_for, keyboard_for_command
from bot.config import Config
from bot.plugins import PluginManager, PluginStateError
from bot.settings import Settings, SettingsError
from bot.orders import OrderStore, OrderMonitorError, fetch_sales, describe_order, order_button
from bot.telegram_api import TelegramAPI, TelegramError

LOG = logging.getLogger('funpayrust')
RUNNING = True
STATE_DIR = Path('/var/lib/funpayrus')


def stop(_signal, _frame):
    global RUNNING
    RUNNING = False




class PollGuard:
    """Serialize periodic and manual requests and throttle forced refreshes."""

    def __init__(self, minimum_interval=20):
        self.lock = threading.Lock()
        self.last_started = 0.0
        self.minimum_interval = minimum_interval

    def enter(self):
        if not self.lock.acquire(blocking=False):
            return False
        now = time.monotonic()
        if now - self.last_started < self.minimum_interval:
            self.lock.release()
            return False
        self.last_started = now
        return True

    def leave(self):
        self.lock.release()


def flush_deliveries(store, settings, api, limit=30):
    """Durably retry unsent Telegram notifications; never leak to revoked roles.

    At-least-once delivery: a process crash between Telegram success and
    local acknowledgement may produce a duplicate, but never silently
    discards the notification before Telegram accepts it.
    """
    sent = 0
    try:
        entries = store.pending_deliveries(limit=limit)
    except OrderMonitorError as exc:
        LOG.warning('Очередь уведомлений: %s', exc)
        return 0
    for event_id, uid, kind, previous_status, order in entries:
        if settings.role(uid) not in ('owner', 'admin') or not settings.order_alert_enabled(uid):
            store.mark_delivered(event_id)
            continue
        button = order_button(order.order_id)
        markup = {'inline_keyboard': [[button]]} if button else None
        if kind == 'paid':
            title = '🛒 Новый оплаченный заказ'
        else:
            old = previous_status or 'неизвестно'
            title = f'🔄 Статус заказа изменился: {old} → {order.status}'
        try:
            api.send(uid, title + '\\n\\n' + describe_order(order), markup)
        except TelegramError:
            LOG.warning('Уведомление о заказе не доставлено; повторим позднее')
            continue
        store.mark_delivered(event_id)
        sent += 1
    return sent


def poll_orders_once(store, settings, api, key=None, fetcher=fetch_sales, guard=None):
    """One opt-in read-only polling cycle; sends durably queued events."""
    if not settings.monitor_enabled():
        flush_deliveries(store, settings, api)
        return 0
    if guard is not None and not guard.enter():
        flush_deliveries(store, settings, api)
        return 0
    try:
        key = os.environ.get('FUNPAY_GOLDEN_KEY') if key is None else key
        if not key:
            store.last_error = 'Golden Key отсутствует. Настрой его только на VPS.'
            return 0
        try:
            updates = store.record(
                fetcher(key, user_agent=os.getenv('FUNPAY_USER_AGENT')),
                recipients=settings.order_alert_recipients()
            )
        except OrderMonitorError as exc:
            store.last_error = str(exc)
            return 0
        return len(updates)
    finally:
        if guard is not None:
            guard.leave()
        flush_deliveries(store, settings, api)


def monitor_orders(store, settings, api, stop_event, guard=None):
    """30-second opt-in polling with backoff; alert on repeated failures."""
    failures = 0
    warned = False
    while not stop_event.is_set():
        if not settings.monitor_enabled():
            flush_deliveries(store, settings, api)
            stop_event.wait(4)
            continue
        poll_orders_once(store, settings, api, guard=guard)
        if store.last_error:
            failures += 1
            LOG.warning('Мониторинг заказов: %s', store.last_error)
            if failures >= 3 and not warned:
                try:
                    api.send(settings.owner_id, '⚠️ Мониторинг FunPay: три проверки подряд завершились ошибкой. '
                             'Открой /orders для диагностики. Покупателям ничего не отправлялось.')
                    warned = True
                except TelegramError:
                    LOG.warning('Предупреждение о сбое FunPay не доставлено')
        else:
            if warned:
                try:
                    api.send(settings.owner_id, '✅ Мониторинг FunPay снова получает данные.')
                except TelegramError:
                    pass
            failures = 0
            warned = False
        delay = 300 if store.last_error and 'частоту' in store.last_error else 30
        stop_event.wait(delay)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        config = Config.load()
        checkpoint = UpdateCheckpoint(STATE_DIR / 'telegram-offset.json')
        settings = Settings(config.admin_id, STATE_DIR / 'settings.json')
        orders = OrderStore(STATE_DIR / 'orders.sqlite3')
    except ValueError as exc:
        LOG.error('Ошибка настроек: %s', exc)
        return 2
    except (CheckpointError, SettingsError, OrderMonitorError) as exc:
        LOG.error('Ошибка безопасного хранения: %s', exc)
        return 2

    allow_external = os.getenv('FUNPAYRUS_ALLOW_EXTERNAL_PLUGINS') == '1'
    try:
        plugins = PluginManager(Path(__file__).resolve().parent.parent / 'plugins',
                                STATE_DIR / 'plugins', STATE_DIR / 'plugin-state.json',
                                allow_external=allow_external)
    except PluginStateError as exc:
        LOG.error('Ошибка состояния плагинов: %s', exc)
        return 2
    if not allow_external:
        LOG.warning('Сторонние плагины выключены до ручного разрешения')
    api = TelegramAPI(config.bot_token)
    try:
        me = api.call('getMe')
    except TelegramError as exc:
        LOG.error('Не удалось запустить бота: %s', exc)
        return 1
    LOG.info('FunPay Rust стартовал (Telegram bot id %s)', me.get('id'))
    for uid in settings.alert_recipients():
        try:
            api.send(uid, '✅ FunPay Rust запущен. Служба работает.')
        except TelegramError:
            LOG.warning('Не удалось доставить уведомление об автозапуске (chat id скрыт)')
    stop_event = threading.Event()
    poll_guard = PollGuard()
    def poll_now():
        return poll_orders_once(orders, settings, api, guard=poll_guard)
    threading.Thread(target=monitor_orders, args=(orders, settings, api, stop_event, poll_guard),
                     name='funpay-orders-readonly', daemon=True).start()
    started_at = datetime.now(timezone.utc)
    while RUNNING:
        try:
            for update in api.updates(checkpoint.last_id + 1):
                uid = update.get('update_id')
                try:
                    if not checkpoint.mark(uid):
                        continue
                except CheckpointError as exc:
                    LOG.error('Блокировка обработки: %s', exc)
                    return 2
                message = update.get('message') or {}
                answer = reply_for(message, config.admin_id, plugins, started_at,
                                   settings=settings, orders=orders, poll_now=poll_now)
                if answer:
                    chat_id = message['from']['id']
                    buttons = keyboard_for_command(message.get('text', ''), plugins,
                                                   settings, chat_id, config.admin_id, orders)
                    api.send(chat_id, answer, buttons)
                callback = update.get('callback_query') or {}
                if callback:
                    response = callback_for(callback, config.admin_id, plugins, started_at,
                                            settings=settings, orders=orders, poll_now=poll_now)
                    if response:
                        text, buttons = response
                        chat_id = callback['from']['id']
                        try:
                            api.edit(chat_id, callback['message']['message_id'], text, buttons)
                        except (KeyError, TelegramError):
                            api.send(chat_id, text, buttons)
                    if isinstance(callback.get('id'), str):
                        api.acknowledge(callback['id'])
        except TelegramError as exc:
            LOG.warning('Telegram API: %s; повтор через 5 секунд', exc)
            time.sleep(5)
        except Exception as exc:
            LOG.error('Ошибка обработки события: %s', type(exc).__name__)
            time.sleep(2)
    stop_event.set()
    LOG.info('FunPay Rust остановлен')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
