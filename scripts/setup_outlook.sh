#!/usr/bin/env bash
# Opt-in Outlook metadata monitoring; never stores a Microsoft password.
set -Eeuo pipefail
APP=/opt/funpayrus
CONF=/etc/funpayrus
if [[ $EUID -ne 0 || "$(pwd -P)" != "$APP" ]]; then
  echo 'Запусти: cd /opt/funpayrus && sudo bash scripts/setup_outlook.sh' >&2
  exit 1
fi
if [[ ! -f "$CONF/bot.env" ]] || ! id funpayrus >/dev/null 2>&1; then
  echo 'Сначала установи Telegram-бот через install.sh.' >&2
  exit 1
fi
if [[ -L "$CONF/mail.key" || -L "$CONF/bot.env" ]]; then
  echo 'Небезопасный путь конфигурации.' >&2
  exit 1
fi
if [[ ! -r /dev/tty ]]; then
  echo 'Нужен локальный интерактивный SSH-терминал.' >&2
  exit 1
fi
read -r -p 'Application (client) ID твоего Microsoft-приложения (UUID): ' CLIENT_ID </dev/tty
if [[ ! "$CLIENT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
  echo 'Неверный формат Microsoft Application ID.' >&2
  exit 1
fi
apt-get update
apt-get install -y python3-venv
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/python" -m pip install --requirement "$APP/requirements-mail.txt"
if [[ ! -e "$CONF/mail.key" ]]; then
  "$APP/.venv/bin/python" -c "from bot.mail_auth import generate_key; generate_key('$CONF/mail.key')"
fi
chown root:funpayrus "$CONF/mail.key"
chmod 0640 "$CONF/mail.key"
# Never expose the app ID (not a secret) as a shell environment assignment from input.
umask 077
tmp="$(mktemp "$CONF/.bot-outlook.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
grep -v '^MAIL_CLIENT_ID=' "$CONF/bot.env" > "$tmp" || true
printf 'MAIL_CLIENT_ID=%s\n' "$CLIENT_ID" >> "$tmp"
chown root:funpayrus "$tmp"
chmod 0640 "$tmp"
mv -f "$tmp" "$CONF/bot.env"
trap - EXIT
install -d -m 0755 /etc/systemd/system/funpayrus.service.d
cat > /etc/systemd/system/funpayrus.service.d/outlook-venv.conf <<'UNIT'
[Service]
ExecStart=
ExecStart=/opt/funpayrus/.venv/bin/python -m bot
UNIT
chmod 0644 /etc/systemd/system/funpayrus.service.d/outlook-venv.conf
systemctl daemon-reload
systemctl restart funpayrus
echo 'Установка Outlook-библиотек готова. Мониторинг выключен.'
echo 'В личном SSH открой OAuth:'
echo '  cd /opt/funpayrus'
echo '  sudo -u funpayrus /opt/funpayrus/.venv/bin/python -m bot.mail_auth connect'
echo 'После подключения включи мониторинг командой /mailalerts on в Telegram.'
