"""Private Telegram interface with owner/admin/viewer access boundaries."""
from datetime import datetime, timezone
import re

from bot import __version__
from bot.diagnostics import demo_screen, report, setup_summary
from bot.funpay_probe import status_from_environment
from bot.settings import SettingsError
from bot.orders import OrderMonitorError

_ID = re.compile(r'^[1-9][0-9]{0,18}$')


def role_for(user_id, owner_id, settings=None):
    if settings is not None:
        return settings.role(user_id)
    return 'owner' if type(user_id) is int and user_id == owner_id else None


def allowed(message, admin_id, settings=None):
    user = message.get('from') or {}
    chat = message.get('chat') or {}
    uid = user.get('id')
    return (role_for(uid, admin_id, settings) is not None and
            chat.get('type') == 'private' and chat.get('id') == uid)


def menu(role='owner'):
    rows = [
        [{'text': '📊 Статус', 'callback_data': 'm:status'},
         {'text': '🩺 Диагностика', 'callback_data': 'm:diagnostics'}],
        [{'text': '🔑 FunPay (тест)', 'callback_data': 'm:funpay'},
         {'text': '🎭 Демо', 'callback_data': 'm:demo'}],
        [{'text': '🔔 Уведомления', 'callback_data': 'm:notifications'},
         {'text': '🧭 Настройка', 'callback_data': 'm:setup'}],
    ]
    if role in ('owner', 'admin'):
        rows.append([{'text': '📦 Заказы (тест)', 'callback_data': 'm:orders'}])
    if role == 'owner':
        rows.append([{'text': '👥 Администраторы', 'callback_data': 'm:admins'},
                     {'text': '🧩 Плагины', 'callback_data': 'm:plugins'}])
    rows.append([{'text': '🏓 Связь', 'callback_data': 'm:ping'},
                 {'text': '🏠 Главная', 'callback_data': 'm:home'}])
    return {'inline_keyboard': rows}


def alerts_menu(settings, user_id):
    active = bool(settings and settings.startup_enabled(user_id))
    rows = [[{'text': ('✅ ' if active else '⚪ ') + 'Уведомлять о запуске бота',
              'callback_data': 'a:startup'}]]
    if settings and settings.role(user_id) in ('owner', 'admin'):
        orders = settings.order_alert_enabled(user_id)
        rows.append([{'text': ('✅ ' if orders else '⚪ ') + 'Новые оплаченные заказы',
                      'callback_data': 'a:orders'}])
    rows.append([{'text': '⬅️ Главное меню', 'callback_data': 'm:home'}])
    return {'inline_keyboard': rows}


def orders_menu(settings, uid):
    rows = []
    if settings and settings.role(uid) == 'owner':
        enabled = settings.monitor_enabled()
        rows.append([{'text': ('⏹ Выключить' if enabled else '▶️ Включить') + ' мониторинг',
                      'callback_data': 'o:toggle'}])
    rows.append([{'text': '🔄 Обновить экран', 'callback_data': 'm:orders'}])
    rows.append([{'text': '⬅️ Назад', 'callback_data': 'm:home'}])
    return {'inline_keyboard': rows}


def orders_screen(settings, orders):
    if not settings or orders is None:
        return '📦 Хранилище заказов не настроено.'
    enabled = settings.monitor_enabled()
    result = ['📦 Заказы FunPay — тест только на чтение',
              'Мониторинг: ' + ('✅ включён' if enabled else '⏸ выключен'),
              'Первый успешный опрос сохраняет историю без уведомлений.',
              'Опрос: примерно каждые 90 секунд, только первая страница продаж.']
    if orders.last_error:
        result.append('⚠️ Последняя ошибка: ' + orders.last_error)
    try:
        count, last, items = orders.summary()
        result.append(f'Сохранено заказов: {count}')
        result.append('Последняя проверка: ' +
                      (datetime.fromtimestamp(last, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
                       if last else 'ещё не проводилась'))
        for order in items[:5]:
            result.append(f'#{order.order_id}: {order.status} — {order.description[:65]}')
    except OrderMonitorError:
        result.append('⚠️ Не удалось прочитать историю заказов.')
    result.append('Для владельца: /orders on или /orders off.\n'
                  'Уведомления включаются отдельно в 🔔 Уведомления.\n'
                  'Никаких сообщений покупателям и автовыдачи.')
    return '\n'.join(result)[:3800]


def plugin_menu(plugins):
    rows = [[{'text': ('✅ ' if enabled else '⚪ ') + info.name,
              'callback_data': 'p:toggle:' + info.command}]
            for info, enabled in plugins.list_all_plugins()[:20]]
    rows.append([{'text': '⬅️ Назад', 'callback_data': 'm:home'}])
    return {'inline_keyboard': rows}


def plugins_text(plugins):
    items = plugins.list_all_plugins()
    if not items:
        return '🧩 Плагинов пока нет.'
    return ('🧩 Мои плагины:\n\n' + '\n'.join(
        ('✅ ' if enabled else '⚪ ') + info.name + ' — /' + info.command
        for info, enabled in items) + '\n\nНажми кнопку, чтобы переключить плагин.')


def admins_text(settings):
    if settings is None:
        return 'Управление пользователями сейчас недоступно.'
    lines = [f'• {uid}: {role}' for uid, role in settings.list_admins()]
    return ('👥 Доступ к FunPay Rust\n\n' + '\n'.join(lines) + '\n\n'
            'Добавить: /addadmin 123456789 viewer\n'
            'Роль admin: /addadmin 123456789 admin\n'
            'Удалить: /removeadmin 123456789\n\n'
            'Только владелец меняет роли. Друг должен сам открыть бота и нажать /start.\n'
            'Админы и наблюдатели не могут управлять доступом или исполнять плагины.')


def notifications_text(settings, uid):
    active = bool(settings and settings.startup_enabled(uid))
    return ('🔔 Центр уведомлений\n\n'
            f'Уведомление о запуске бота: {"включено" if active else "выключено"}\n'
            f'Уведомления о заказах: {"включены" if settings and settings.order_alert_enabled(uid) else "выключены"}.\n'
            'Внешняя проверка доступности VPS: ещё не подключена.\n\n'
            'Настройки применяются сразу, без перезапуска.')


def _add_admin(args, settings):
    parts = args.split()
    if len(parts) not in (1, 2) or not _ID.fullmatch(parts[0]):
        return 'Используй: /addadmin <Telegram ID> [viewer|admin]'
    role = parts[1] if len(parts) == 2 else 'viewer'
    if role not in ('admin', 'viewer'):
        return 'Роль должна быть viewer или admin.'
    try:
        ok = settings.add(int(parts[0]), role)
    except SettingsError:
        return '❌ Не удалось безопасно сохранить настройки.'
    return ('✅ Доступ настроен. Попроси пользователя открыть бота и нажать /start.'
            if ok else '❌ Нельзя добавить пользователя. Проверь ID и лимит (5).')


def _remove_admin(args, settings):
    if not _ID.fullmatch(args.strip()):
        return 'Используй: /removeadmin <Telegram ID>'
    try:
        ok = settings.remove(int(args.strip()))
    except SettingsError:
        return '❌ Не удалось безопасно сохранить настройки.'
    return '✅ Доступ отозван.' if ok else '❌ Пользователь не найден или владелец.'


def reply_for(message, admin_id, plugins, started_at,
              funpay_status=status_from_environment, settings=None, orders=None):
    if not allowed(message, admin_id, settings):
        return None
    text = message.get('text')
    if not isinstance(text, str) or not text.strip().startswith('/'):
        return None
    parts = text.strip().split(maxsplit=1)
    command = parts[0][1:].split('@')[0].lower()
    args = parts[1] if len(parts) == 2 else ''
    uid = message['from']['id']
    role = role_for(uid, admin_id, settings)

    if command in ('start', 'help', 'menu'):
        return (f'🐱 FunPay Rust v{__version__}\n\n'
                f'Твоя роль: {role}\n\n'
                '/status — состояние\n/diagnostics — диагностика VPS\n'
                '/notifications — центр уведомлений\n/demo — демо-режим\n'
                '/setup — мастер настройки\n/funpay — тест соединения\n'
                + ('/orders — мониторинг продаж (только чтение)\n' if role in ('owner', 'admin') else '')
                + ('/admins — управление доступом\n/plugins — плагины\n' if role == 'owner' else '')
                + '\n⚠️ Автовыдача и отправка сообщений покупателям отключены.')
    if command == 'status':
        seconds = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
        return (f'✅ FunPay Rust v{__version__} работает\n'
                f'⏱ Время работы: {seconds // 60} мин.\n'
                f'👤 Роль: {role}\n'
                f'🧩 Активных плагинов: {len(plugins.commands)}')
    if command == 'ping':
        return '🏓 Pong! Бот на связи.'
    if command == 'diagnostics':
        return report(plugins, settings)
    if command == 'setup':
        return setup_summary(settings)
    if command == 'demo':
        return demo_screen()
    if command == 'notifications':
        return notifications_text(settings, uid)
    if command == 'funpay':
        return funpay_status() if role in ('owner', 'admin') else '🔒 Проверка FunPay доступна администраторам.'
    if command == 'orders':
        if role not in ('owner', 'admin'):
            return '🔒 Заказы доступны только администраторам.'
        if args.strip() in ('on', 'off'):
            if role != 'owner' or settings is None:
                return '🔒 Только владелец включает мониторинг.'
            try:
                settings.set_monitor(args.strip() == 'on')
            except SettingsError:
                return '❌ Не удалось сохранить режим мониторинга.'
        elif args.strip():
            return 'Используй: /orders [on|off]'
        return orders_screen(settings, orders)
    if command == 'admins':
        return admins_text(settings) if role == 'owner' else '🔒 Только владелец управляет доступом.'
    if command == 'addadmin':
        return _add_admin(args, settings) if role == 'owner' and settings else '🔒 Только владелец управляет доступом.'
    if command == 'removeadmin':
        return _remove_admin(args, settings) if role == 'owner' and settings else '🔒 Только владелец управляет доступом.'
    if command == 'plugins':
        return plugins_text(plugins) if role == 'owner' else '🔒 Доступ к плагинам ограничен владельцем.'
    return plugins.execute(command, args) if role == 'owner' else None


def keyboard_for_command(text, plugins, settings=None, user_id=None, owner_id=None):
    command = text.strip().split(maxsplit=1)[0].split('@')[0].lower() if text and text.strip() else ''
    if command == '/plugins' and role_for(user_id, owner_id, settings) == 'owner':
        return plugin_menu(plugins)
    if command == '/notifications':
        return alerts_menu(settings, user_id)
    if command == '/orders':
        return orders_menu(settings, user_id)
    return menu(role_for(user_id, owner_id, settings))


def callback_for(callback, admin_id, plugins, started_at,
                 funpay_status=status_from_environment, settings=None, orders=None):
    check = {'from': callback.get('from'), 'chat': (callback.get('message') or {}).get('chat')}
    if not allowed(check, admin_id, settings):
        return None
    uid = check['from']['id']
    role = role_for(uid, admin_id, settings)
    data = callback.get('data')
    sections = {'m:home': '/start', 'm:help': '/start', 'm:status': '/status',
                'm:ping': '/ping', 'm:funpay': '/funpay',
                'm:plugins': '/plugins', 'm:diagnostics': '/diagnostics',
                'm:setup': '/setup', 'm:demo': '/demo',
                'm:notifications': '/notifications', 'm:admins': '/admins',
                'm:orders': '/orders'}
    if data in sections:
        command = sections[data]
        output = reply_for({**check, 'text': command}, admin_id, plugins, started_at,
                           funpay_status=funpay_status, settings=settings, orders=orders)
        return output, keyboard_for_command(command, plugins, settings, uid, admin_id)
    if data == 'o:toggle':
        if role != 'owner' or settings is None:
            return '🔒 Только владелец включает мониторинг.', menu(role)
        try:
            settings.set_monitor(not settings.monitor_enabled())
        except SettingsError:
            return '❌ Не удалось сохранить режим мониторинга.', orders_menu(settings, uid)
        return orders_screen(settings, orders), orders_menu(settings, uid)
    if data == 'a:orders':
        if role not in ('owner', 'admin') or settings is None:
            return '🔒 Уведомления о заказах недоступны.', menu(role)
        try:
            settings.toggle_order_alert(uid)
        except SettingsError:
            return '❌ Не удалось сохранить уведомления.', alerts_menu(settings, uid)
        return notifications_text(settings, uid), alerts_menu(settings, uid)
    if data == 'a:startup':
        try:
            if not settings or not settings.toggle_startup(uid):
                return '❌ Не удалось изменить настройки.', alerts_menu(settings, uid)
        except SettingsError:
            return '❌ Не удалось сохранить настройки.', alerts_menu(settings, uid)
        return notifications_text(settings, uid), alerts_menu(settings, uid)
    if isinstance(data, str) and data.startswith('p:toggle:'):
        if role != 'owner':
            return '🔒 Только владелец управляет плагинами.', menu(role)
        command = data[len('p:toggle:'):]
        if command not in plugins.available:
            return '❌ Плагин не найден.', plugin_menu(plugins)
        if not plugins.set_enabled(command, command not in plugins.commands):
            return '❌ Не удалось сохранить состояние плагина.', plugin_menu(plugins)
        return plugins_text(plugins), plugin_menu(plugins)
    return None
