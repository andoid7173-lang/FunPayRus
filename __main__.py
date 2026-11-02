"""Run the admin bot with Telegram long polling."""
from datetime import datetime, timezone
import logging
from pathlib import Path
import signal
import time

from bot.commands import reply_for
from bot.config import Config
from bot.plugins import PluginManager
from bot.telegram_api import TelegramAPI, TelegramError

LOG = logging.getLogger("funpayrus")
RUNNING = True


def stop(_signal, _frame):
    global RUNNING
    RUNNING = False


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        config = Config.load()
    except ValueError as exc:
        LOG.error("Ошибка настроек: %s", exc)
        return 2
    plugins = PluginManager(
        Path(__file__).resolve().parent.parent / "plugins",
        Path("/var/lib/funpayrus/plugins"),
    )
    for err in plugins.errors:
        LOG.warning("Не удалось загрузить плагин %s", err)
    api = TelegramAPI(config.bot_token)
    try:
        me = api.call("getMe")
    except TelegramError as exc:
        LOG.error("Невозможно запустить бота: %s", exc)
        return 1
    LOG.info("FunPay Rus стартовал (Telegram bot id %s; плагинов: %s)", me.get("id"), len(plugins.commands))
    started_at = datetime.now(timezone.utc)
    offset = None
    while RUNNING:
        try:
            updates = api.updates(offset)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                message = update.get("message") or {}
                answer = reply_for(message, config.admin_id, plugins, started_at)
                if answer:
                    api.send(config.admin_id, answer)
        except TelegramError as exc:
            LOG.warning("Telegram API: %s; повтор через 5 секунд", exc)
            time.sleep(5)
        except Exception as exc:
            # Never print token or full HTTP request URLs.
            LOG.error("Ошибка обработки события: %s", type(exc).__name__)
            time.sleep(2)
    LOG.info("FunPay Rus остановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
