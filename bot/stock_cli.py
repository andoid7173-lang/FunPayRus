"""VPS-only digital-code inventory commands; no secrets in shell args or Telegram."""
import getpass
import os
import sys
from pathlib import Path

from bot.orders import OrderStore
from bot.stock import StockError, StockStore


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ('add', 'show', 'list'):
        print('Используй: python3 -m bot.stock_cli add SKU | show ORDER_ID | list')
        return 2
    path = Path(os.environ.get('FUNPAYRUS_STATE_DIR', '/var/lib/funpayrus')) / 'orders.sqlite3'
    if not path.is_file():
        print('Сначала установи и запусти Telegram-бота.', file=sys.stderr)
        return 2
    try:
        # Repair/migrate schema before accessing stock on a deployed instance.
        OrderStore(path)
        store = StockStore(path)
        command = argv[0]
        if command == 'list' and len(argv) == 1:
            for sku, stats in store.summary().items():
                print(f'{sku}: доступно {stats["available"]}, зарезервировано '
                      f'{stats["reserved"]}, выдано {stats["issued"]}, списано {stats["void"]}')
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print('Требуется локальный интерактивный SSH-терминал.', file=sys.stderr)
            return 2
        if command == 'add' and len(argv) == 2:
            if not store.valid_sku(argv[1]):
                print('SKU: 2–40 символов A–Z, 0–9, _ и -.', file=sys.stderr)
                return 2
            value = getpass.getpass('Вставь код товара (скрытый ввод): ')
            result = store.add(argv[1], value)
            value = None
            print({'added': 'Товар добавлен.', 'duplicate': 'Такой код уже в складе.',
                   'invalid': 'Некорректный код.'}[result])
            return 0 if result == 'added' else 2
        if command == 'show' and len(argv) == 2:
            value = store.secret_for_local_terminal(argv[1])
            if value is None:
                print('Резервирование не найдено или уже завершено.')
                return 2
            print('Внимание: сверяй оплату на FunPay. Код виден только в этом терминале.')
            print(value)
            return 0
        print('Неверные аргументы.', file=sys.stderr)
        return 2
    except StockError:
        print('Ошибка безопасного доступа к локальному складу.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
