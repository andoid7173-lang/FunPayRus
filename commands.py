"""Admin-only command handling."""
from datetime import datetime, timezone
from bot import __version__


def reply_for(message: dict, admin_id: int, plugins, started_at: datetime):
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    if sender.get("id") != admin_id:
        return None
    # Never handle privileged commands in groups, even if the admin is present.
    if chat.get("type") != "private" or chat.get("id") != admin_id:
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip().startswith("/"):
        return None
    parts = text.strip().split(maxsplit=1)
    command = parts[0][1:].split("@")[0].lower()
    args = parts[1] if len(parts) == 2 else ""
    if command in ("start", "help"):
        return (
            "🐱 FunPay Rus — версия " + __version__ + "\n\n"
            "👋 Привет! Твой бот успешно работает на VPS.\n\n"
            "Доступные команды:\n"
            "/status — состояние бота\n"
            "/plugins — установленные плагины\n"
            "/ping — проверка связи\n"
            "/help — справка\n\n"
            "ℹ️ Интеграция с FunPay пока не реализована."
        )
    if command == "ping":
        return "🏓 Pong! Бот на связи."
    if command == "status":
        seconds = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
        return f"✅ FunPay Rus {__version__} работает\n⏱ Время работы: {seconds // 60} мин.\n🧩 Плагинов: {len(plugins.commands)}"
    if command == "plugins":
        items = plugins.list_plugins()
        if not items:
            return "🧩 Подключённых плагинов пока нет."
        return "🧩 Плагины:\n" + "\n".join(
            f"• {info.name} v{info.version} — /{info.command}" for info in items
        )
    return plugins.execute(command, args)
