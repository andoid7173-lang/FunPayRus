#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/funpayrus"
CONF_DIR="/etc/funpayrus"
CONF_FILE="$CONF_DIR/bot.env"
SERVICE_FILE="/etc/systemd/system/funpayrus.service"

if [[ "$EUID" -ne 0 ]]; then
  echo '❌ Запустите: sudo bash install.sh' >&2
  exit 1
fi
if [[ "$(pwd -P)" != "$APP_DIR" ]]; then
  echo "❌ Проект должен находиться в $APP_DIR" >&2
  exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
  echo '❌ Требуется Ubuntu/Debian с systemd.' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
  echo '❌ Нужен Python 3.10 или новее.' >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo '❌ Не найден Git.' >&2
  exit 1
fi
APP_VERSION="$(python3 -c 'from bot import __version__; print(__version__)')"
printf '\n🐱 FunPay Rus — установщик v%s\n' "$APP_VERSION"
printf '%s\n' '============================================'
printf '%s\n' 'Для настройки нужны Telegram Bot Token от @BotFather'
printf '%s\n' 'и числовой Telegram ID администратора.'
printf '%s\n' 'Токены и Golden Key вводите только на своём VPS.'
printf '%s\n' '============================================'

if ! id -u funpayrus >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/funpayrus --shell /usr/sbin/nologin funpayrus
fi
install -d -m 0750 -o funpayrus -g funpayrus /var/lib/funpayrus
install -d -m 0750 -o funpayrus -g funpayrus /var/lib/funpayrus/plugins
install -d -m 0750 -o root -g funpayrus "$CONF_DIR"
if [[ -L "$CONF_FILE" ]]; then
  echo '❌ Файл конфигурации является символьной ссылкой. Операция остановлена.' >&2
  exit 1
fi
if [[ -f "$CONF_FILE" && "${1:-}" != "--reconfigure" ]]; then
  chown root:funpayrus "$CONF_FILE"
  chmod 0640 "$CONF_FILE"
  echo '🔐 Существующая конфигурация сохранена без изменений.'
else
  if [[ ! -r /dev/tty ]]; then
    echo '❌ Нужен интерактивный терминал /dev/tty.' >&2
    exit 1
  fi
  echo 'ℹ️ Создайте бота у @BotFather: https://t.me/BotFather'
  read -r -s -p '🤖 Telegram Bot Token (скрытый ввод): ' TOKEN </dev/tty
  printf '\n'
  read -r -p '👤 Числовой Telegram Admin ID: ' ADMIN_ID </dev/tty
  if [[ ! "$TOKEN" =~ ^[0-9]{5,15}:[a-zA-Z0-9_-]{30,}$ ]]; then
    echo '❌ Неверный формат Telegram Bot Token.' >&2
    exit 1
  fi
  if [[ ! "$ADMIN_ID" =~ ^[0-9]+$ || "$ADMIN_ID" =~ ^0+$ ]]; then
    echo '❌ Неверный Telegram Admin ID.' >&2
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
  echo '✅ Настройки сохранены вне GitHub.'
fi

# A separate optional EnvironmentFile lets the test run without FunPay enabled.
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
EnvironmentFile=-/etc/funpayrus/funpay.env
ExecStart=/usr/bin/python3 -m bot
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
RestrictSUIDSGID=true
RestrictRealtime=true
RestrictNamespaces=true
LockPersonality=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
AmbientCapabilities=
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
  echo 'Telegram: /start; тест подключения FunPay: /funpay'
  echo 'Проверка журнала: sudo journalctl -u funpayrus -n 30 --no-pager'
else
  echo '❌ Сервис не запустился. Проверьте журнал:' >&2
  echo 'sudo journalctl -u funpayrus -n 30 --no-pager' >&2
  exit 1
fi
