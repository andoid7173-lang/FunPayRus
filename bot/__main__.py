"""Run Telegram admin bot with long polling."""
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import signal
import time

from bot.checkpoint import CheckpointError, UpdateCheckpoint
from bot.commands import reply_for, callback_for, menu, plugin_menu
from bot.config import Config
from bot.plugins import PluginManager, PluginStateError
from bot.telegram_api import TelegramAPI, TelegramError

LOG = logging.getLogger("funpayrus")
RUNNING = True
STATE_DIR = Path("/var/lib/funpayrus")


def stop(_signal, _frame):
    global RUNNING
    RUNNING = False


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        config = Config.load()
        checkpoint = UpdateCheckpoint(STATE_DIR / "telegram-offset.json")
    except ValueError as exc:
        LOG.error("Ошибка настроек: %s", exc)
        return 2
    except CheckpointError as exc:
        LOG.error("Ошибка хранения Telegram: %s", exc)
        return 2

    allow_external = os.getenv("FUNPAYRUS_ALLOW_EXTERNAL_PLUGINS") == "1"
    try:
        plugins = PluginManager(
            Path(__file__).resolve().parent.parent / "plugins",
            STATE_DIR / "plugins",
            STATE_DIR / "plugin-state.json",
            allow_external=allow_external,
        )
    except PluginStateError as exc:
        LOG.error("Ошибка состояния плагинов: %s", exc)
        return 2
    if not allow_external:
        LOG.warning("Сторонние плагины выключены до ручного разрешения")
    api = TelegramAPI(config.bot_token)
    try:
        me = api.call("getMe")
    except TelegramError as exc:
        LOG.error("Не удалось запустить бота: %s", exc)
        return 1
    LOG.info("FunPay Rus стартовал (Telegram bot id %s)", me.get("id"))
    started_at = datetime.now(timezone.utc)
    while RUNNING:
        try:
            for update in api.updates(checkpoint.last_id + 1):
                uid = update.get("update_id")
                # Checkpoint before any side effects; prevents repeated toggles on restart.
                try:
                    if not checkpoint.mark(uid):
                        continue
                except CheckpointError as exc:
                    LOG.error("Блокировка обработки: %s", exc)
                    return 2  # fail closed instead of executing uncheckpointed actions
                message = update.get("message") or {}
                answer = reply_for(message, config.admin_id, plugins, started_at)
                if answer:
                    command = (message.get("text") or "").split(maxsplit=1)[0].split("@")[0].lower()
                    buttons = plugin_menu(plugins) if command == "/plugins" else menu()
                    api.send(config.admin_id, answer, buttons)
                callback = update.get("callback_query") or {}
                if callback:
                    response = callback_for(callback, config.admin_id, plugins, started_at)
                    if response:
                        text, buttons = response
                        try:
                            api.edit(config.admin_id, callback["message"]["message_id"], text, buttons)
                        except (KeyError, TelegramError):
                            api.send(config.admin_id, text, buttons)
                    if isinstance(callback.get("id"), str):
                        api.acknowledge(callback["id"])
        except TelegramError as exc:
            LOG.warning("Telegram API: %s; повтор через 5 секунд", exc)
            time.sleep(5)
        except Exception as exc:
            LOG.error("Ошибка обработки события: %s", type(exc).__name__)
            time.sleep(2)
    LOG.info("FunPay Rus остановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
