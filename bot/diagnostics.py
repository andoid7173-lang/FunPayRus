"""Non-invasive local checks: never show credentials or private server paths."""
import os
from pathlib import Path
import shutil
import sys


def report(plugins, settings=None, state_dir=Path('/var/lib/funpayrus'), env=None):
    env = os.environ if env is None else env
    state_dir = Path(state_dir)
    try:
        free = shutil.disk_usage(state_dir if state_dir.exists() else '/').free // (1024 ** 3)
        disk = f'{free} ГиБ свободно'
    except OSError:
        disk = 'не удалось проверить'
    try:
        storage = 'доступно' if state_dir.is_dir() and os.access(state_dir, os.W_OK) else 'недоступно'
    except OSError:
        storage = 'недоступно'
    errors = len(getattr(plugins, 'errors', []))
    external = 'разрешены вручную' if getattr(plugins, 'allow_external', False) else 'отключены'
    configured = bool(env.get('FUNPAY_GOLDEN_KEY'))
    return ('🩺 FunPay Rust — диагностика\n\n'
            '✅ Telegram: бот отвечает (это не внешний мониторинг)\n'
            f'🔑 FunPay: {"ключ настроен, связь не проверялась" if configured else "ключ не указан"}\n'
            f'💾 Хранилище: {storage}\n'
            f'💽 Диск: {disk}\n'
            f'🐍 Python: {sys.version_info.major}.{sys.version_info.minor}\n'
            f'🧩 Плагины: {len(getattr(plugins, "commands", {}))} активных; ошибок: {errors}\n'
            f'🔒 Сторонние плагины: {external}\n\n'
            'ℹ️ systemd автоматически перезапускает службу при ошибке. '
            'Полное отключение VPS отслеживается только внешним мониторингом.')


def setup_summary(settings=None, env=None):
    env = os.environ if env is None else env
    total = len(settings.list_admins()) if settings else 1
    golden = bool(env.get('FUNPAY_GOLDEN_KEY'))
    return ('🧭 FunPay Rust — мастер настройки\n\n'
            '✅ Telegram: запущен\n'
            f'✅ Пользователей с доступом: {total}\n'
            f'{"✅" if golden else "⚪"} FunPay Golden Key: {"указан" if golden else "не настроен"}\n'
            '⚪ Заказы: ещё не подключены\n'
            '⚪ Каталог плагинов: в разработке\n\n'
            '🔐 Чтобы настроить Golden Key, используйте только терминал VPS:\n'
            'sudo bash /opt/funpayrus/scripts/configure_funpay.sh\n'
            'После изменения ключа потребуется автоматический перезапуск службы.\n'
            'Другие настройки ниже применяются без перезапуска.')


def demo_screen():
    return ('🎭 ДЕМОНСТРАЦИЯ — FunPay Rust\n\n'
            'Данные вымышлены. Соединения с FunPay и операций с заказами нет.\n\n'
            '📦 Пример заказа #DEMO-001 — 499 ₽\n'
            '📦 Пример заказа #DEMO-002 — 199 ₽\n'
            '💬 Пример нового сообщения покупателя\n\n'
            'Это только демонстрация интерфейса.')
