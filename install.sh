#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/funpayrus"
CONF_DIR="/etc/funpayrus"
CONF_FILE="$CONF_DIR/bot.env"
SERVICE_FILE="/etc/systemd/system/funpayrus.service"


if [[ "$EUID" -ne 0 ]]; then
  echo '❌ Запустите установщик через sudo: sudo bash install.sh' >&2
  exit 1
fi
if [[ "$(pwd -P)" != "$APP_DIR" ]]; then
  echo "❌ Проект должен находиться в $APP_DIR. Смотрите README.md" >&2
  exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
  echo '❌ Требуется Ubuntu/Debian с systemd.' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo '❌ Не найден Python 3. Установите: sudo apt install python3' >&2
  exit 1
fi
if ! python3 -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
  echo '❌ Нужен Python 3.10 или новее.' >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo '❌ Не найден Git. Установите: sudo apt install git' >&2
  exit 1
fi
APP_VERSION="$(python3 -c 'from bot import __version__; print(__version__)')"
printf '\n🐱 FunPay Rus — установщик v%s\n' "$APP_VERSION"
printf '%s\n' '==================================================='
printf '%s\n' '👋 Добро пожаловать! Настроим вашего Telegram-бота.'
printf '%s\n' 'Для установки понадобятся:'
printf '%s\n' '  1. Telegram Bot Token — получите у @BotFather через /newbot'
printf '%s\n' '     https://t.me/BotFather'
printf '%s\n' '  2. Числовой Telegram ID администратора (не @username).'
printf '%s\n' '🔐 Секреты останутся только на вашем VPS.'
printf '%s\n' '==================================================='

if ! id -u funpayrus >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/funpayrus --shell /usr/sbin/nologin funpayrus
fi
install -d -m 0750 -o funpayrus -g funpayrus /var/lib/funpayrus
install -d -m 0750 -o funpayrus -g funpayrus /var/lib/funpayrus/plugins
install -d -m 0750 -o root -g funpayrus "$CONF_DIR"

if [[ -f "$CONF_FILE" && "${1:-}" != "--reconfigure" ]]; then
  echo '🔐 Настройки уже существуют, используем их без изменений.'
  echo 'Чтобы изменить их: sudo bash install.sh --reconfigure'
else
  if [[ ! -e /dev/tty ]]; then
    echo '❌ Для безопасного ввода токена нужен интерактивный терминал.' >&2
    exit 1
  fi
  echo '🔐 Настройка: данные сохранятся ТОЛЬКО в /etc/funpayrus/bot.env'
  echo 'ℹ️ Создайте бота у @BotFather и скопируйте выданный API Token.'
  read -r -s -p '🤖 Введите API Token от @BotFather (ввод скрыт): ' TOKEN </dev/tty
  printf '\n'
  read -r -p '👤 Введите ваш числовой Telegram ID администратора: ' ADMIN_ID </dev/tty
  if [[ ! "$TOKEN" =~ ^[0-9]{5,15}:[a-zA-Z0-9_-]{30,}$ ]]; then
    echo '❌ Формат токена некорректный, данные не сохранены.' >&2
    exit 1
  fi
  if [[ ! "$ADMIN_ID" =~ ^[0-9]+$ ]] || [[ "$ADMIN_ID" == 0 ]]; then
    echo '❌ ID должен быть положительным числом.' >&2
    exit 1
  fi
  umask 077
  TMPFILE="$(mktemp "$CONF_DIR/.bot.env.XXXXXX")"
  trap 'rm -f "$TMPFILE"' EXIT
  printf 'BOT_TOKEN=%s\nADMIN_ID=%s\n' "$TOKEN" "$ADMIN_ID" > "$TMPFILE"
  chown root:funpayrus "$TMPFILE"
  chmod 0640 "$TMPFILE"
  mv -f "$TMPFILE" "$CONF_FILE"
  trap - EXIT
  unset TOKEN ADMIN_ID
  echo '✅ Настройки сохранены. Они находятся вне GitHub-репозитория.'
fi

cat > "$SERVICE_FILE" <<'UNIT'
[Unit]
Description=FunPay Rus Telegram Admin Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=funpayrus
Group=funpayrus
WorkingDirectory=/opt/funpayrus
EnvironmentFile=/etc/funpayrus/bot.env
ExecStart=/usr/bin/python3 -m bot
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/funpayrus

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable funpayrus >/dev/null
systemctl restart funpayrus
sleep 3
if systemctl is-active --quiet funpayrus; then
  printf '\n✅ FunPay Rus v%s запущен!\n' "$APP_VERSION"
  printf 'Откройте своего Telegram-бота и отправьте /start\n'
  printf 'Статус: sudo systemctl status funpayrus --no-pager\n'
  printf 'Журнал: sudo journalctl -u funpayrus -n 30 --no-pager\n'
else
  echo '❌ Бот не запустился. Проверьте статус и журнал:' >&2
  echo 'sudo systemctl status funpayrus --no-pager' >&2
  echo 'sudo journalctl -u funpayrus -n 30 --no-pager' >&2
  exit 1
fi
