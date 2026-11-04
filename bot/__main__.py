"""Run Telegram admin bot with private role-aware dispatch and long polling."""
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import signal
import time

from bot.checkpoint import CheckpointError, UpdateCheckpoint
from bot.commands import reply_for, callback_for, keyboard_for_command
from bot.config import Config
from bot.plugins import PluginManager, PluginStateError
from bot.settings import Settings, SettingsError
from bot.telegram_api import TelegramAPI, TelegramError

LOG = logging.getLogger('funpayrust')
RUNNING = True
STATE_DIR = Path('/var/lib/funpayrus')


def stop(_signal, _frame):
    global RUNNING
    RUNNING = False


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        config = Config.load()
        checkpoint = UpdateCheckpoint(STATE_DIR / 'telegram-offset.json')
        settings = Settings(config.admin_id, STATE_DIR / 'settings.json')
    except ValueError as exc:
        LOG.error('Ошибка настроек: %s', exc)
        return 2
    except (CheckpointError, SettingsError) as exc:
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
                                   settings=settings)
                if answer:
                    chat_id = message['from']['id']
                    buttons = keyboard_for_command(message.get('text', ''), plugins,
                                                   settings, chat_id, config.admin_id)
                    api.send(chat_id, answer, buttons)
                callback = update.get('callback_query') or {}
                if callback:
                    response = callback_for(callback, config.admin_id, plugins, started_at,
                                            settings=settings)
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
    LOG.info('FunPay Rust остановлен')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
