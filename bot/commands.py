"""Private admin-only menu and callback handling."""
from datetime import datetime, timezone
from bot import __version__
from bot.funpay_probe import status_from_environment


def allowed(message, admin_id):
    user = message.get("from") or {}
    chat = message.get("chat") or {}
    return (user.get("id") == admin_id and chat.get("type") == "private"
            and chat.get("id") == admin_id)


def menu():
    return {"inline_keyboard": [
        [{"text": "📊 Статус", "callback_data": "m:status"},
         {"text": "🧩 Плагины", "callback_data": "m:plugins"},
         {"text": "🔑 FunPay (тест)", "callback_data": "m:funpay"}],
        [{"text": "🏓 Связь", "callback_data": "m:ping"},
         {"text": "🏠 Главная", "callback_data": "m:home"}]]}


def plugin_menu(plugins):
    rows = [[{"text": ("✅ " if enabled else "⚪ ") + info.name,
             "callback_data": "p:toggle:" + info.command}]
            for info, enabled in plugins.list_all_plugins()[:20]]
    rows.append([{"text": "⬅️ Назад", "callback_data": "m:home"}])
    return {"inline_keyboard": rows}


def plugins_text(plugins):
    items = plugins.list_all_plugins()
    if not items:
        return "🧩 Плагинов пока нет."
    return "🧩 Мои плагины:\n\n" + "\n".join(
        ("✅ " if enabled else "⚪ ") + info.name + " — /" + info.command
        for info, enabled in items
    ) + "\n\nНажми кнопку, чтобы включить или отключить плагин."


def reply_for(message, admin_id, plugins, started_at, funpay_status=status_from_environment):
    if not allowed(message, admin_id):
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip().startswith("/"):
        return None
    parts = text.strip().split(maxsplit=1)
    command = parts[0][1:].split("@")[0].lower()
    args = parts[1] if len(parts) == 2 else ""
    if command in ("start", "help", "menu"):
        return (f"🐱 FunPay Rus v{__version__}\n\nУправление через кнопки.\n"
                "/status — состояние\n/plugins — плагины\n/ping — связь\n"
                "/funpay — тест подключения (только чтение)\n\n"
                "⚠️ Автовыдача и обработка заказов пока отключены.")
    if command == "status":
        seconds = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
        return (f"✅ FunPay Rus {__version__} работает\n"
                f"⏱ Время работы: {seconds // 60} мин.\n"
                f"🧩 Плагинов активно: {len(plugins.commands)}")
    if command == "ping":
        return "🏓 Pong! Бот на связи."
    if command == "plugins":
        return plugins_text(plugins)
    if command == "funpay":
        return funpay_status()
    return plugins.execute(command, args)


def callback_for(callback, admin_id, plugins, started_at, funpay_status=status_from_environment):
    check = {"from": callback.get("from"), "chat": (callback.get("message") or {}).get("chat")}
    if not allowed(check, admin_id):
        return None
    data = callback.get("data")
    if data in ("m:home", "m:help"):
        return reply_for({**check, "text": "/start"}, admin_id, plugins, started_at,
                         funpay_status), menu()
    if data == "m:status":
        return reply_for({**check, "text": "/status"}, admin_id, plugins, started_at,
                         funpay_status), menu()
    if data == "m:ping":
        return "🏓 Pong! Бот на связи.", menu()
    if data == "m:funpay":
        return funpay_status(), menu()
    if data == "m:plugins":
        return plugins_text(plugins), plugin_menu(plugins)
    if isinstance(data, str) and data.startswith("p:toggle:"):
        command = data[len("p:toggle:"):]
        if command not in plugins.available:
            return "❌ Плагин не найден.", plugin_menu(plugins)
        if not plugins.set_enabled(command, command not in plugins.commands):
            return "❌ Не удалось сохранить состояние плагина.", plugin_menu(plugins)
        return plugins_text(plugins), plugin_menu(plugins)
    return None
