#!/usr/bin/env bash
set -Eeuo pipefail

CONF_DIR=/etc/funpayrus
CONF_FILE="$CONF_DIR/funpay.env"
if [[ "$EUID" -ne 0 ]]; then
  echo 'Запустите: sudo bash scripts/configure_funpay.sh' >&2
  exit 1
fi
if [[ ! -d "$CONF_DIR" ]] || ! getent group funpayrus >/dev/null; then
  echo 'Сначала установите FunPay Rus через install.sh.' >&2
  exit 1
fi
if [[ -L "$CONF_FILE" ]]; then
  echo 'Небезопасный путь к файлу конфигурации (ссылка).' >&2
  exit 1
fi
if [[ "${1:-}" == '--remove' ]]; then
  # Avoid shell history exposure; only removes the optional FunPay key file.
  rm -f -- "$CONF_FILE"
  echo 'Golden Key удалён из конфигурации VPS. Для применения перезапустите службу.'
  exit 0
fi
if [[ "${1:-}" != '' ]]; then
  echo 'Использование: sudo bash scripts/configure_funpay.sh [--remove]' >&2
  exit 1
fi
if [[ ! -r /dev/tty ]]; then
  echo 'Нужен интерактивный терминал.' >&2
  exit 1
fi
printf '%s\n' '🔑 FunPay (экспериментальное соединение только для чтения)'
printf '%s\n' 'Ключ останется только в /etc/funpayrus/funpay.env.'
printf '%s\n' 'Не отправляйте Golden Key в Telegram, GitHub или сюда в чат.'
read -r -s -p 'Golden Key (символы скрыты): ' KEY </dev/tty
printf '\n'
if [[ ! "$KEY" =~ ^[A-Za-z0-9_-]{16,256}$ ]]; then
  echo 'Неверный формат Golden Key; данные не записаны.' >&2
  exit 1
fi
umask 077
TMPFILE="$(mktemp "$CONF_DIR/.funpay.env.XXXXXX")"
trap 'rm -f -- "$TMPFILE"' EXIT
printf 'FUNPAY_GOLDEN_KEY=%s\n' "$KEY" > "$TMPFILE"
chown root:funpayrus "$TMPFILE"
chmod 0640 "$TMPFILE"
mv -f "$TMPFILE" "$CONF_FILE"
trap - EXIT
unset KEY
echo '✅ Golden Key сохранён локально.'
echo 'Примените настройку: sudo systemctl restart funpayrus'
echo 'Проверка через личный Telegram-чат: /funpay'
