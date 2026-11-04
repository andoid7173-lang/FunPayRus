#!/usr/bin/env bash
# Read-only local security diagnostics. Never print secret values.
set -Eeuo pipefail

cd /opt/funpayrus
printf '🔐 FunPay Rus — проверка базовой защиты\n'
if git ls-files | grep -Eq '(^|/)(bot\.env|funpay\.env|\.env|plugin-state\.json)$'; then
  echo '❌ В Git отслеживается файл с потенциальными секретами.' >&2
  exit 1
fi
for file in /etc/funpayrus/bot.env /etc/funpayrus/funpay.env; do
  [[ -e "$file" || -L "$file" ]] || continue
  if [[ -L "$file" ]]; then
    echo "❌ $file является символьной ссылкой." >&2
    exit 1
  fi
  mode="$(stat -c %a "$file")"
  owner="$(stat -c %U:%G "$file")"
  if [[ "$mode" != '640' || "$owner" != 'root:funpayrus' ]]; then
    echo "⚠️ Проверьте права для $file: $mode, $owner" >&2
    exit 1
  fi
  echo "✅ $file: права и владелец соответствуют ожидаемым"
done
if [[ -L /var/lib/funpayrus/plugin-state.json || -L /var/lib/funpayrus/telegram-offset.json ]]; then
  echo '❌ Небезопасная ссылка среди файлов состояния.' >&2
  exit 1
fi
if systemctl is-enabled --quiet funpayrus && systemctl is-active --quiet funpayrus; then
  echo '✅ Служба активна и включена в автозапуск'
else
  echo '⚠️ Служба неактивна или автозапуск выключен'
fi
systemctl show funpayrus --property=NoNewPrivileges --property=ProtectSystem --property=ProtectHome --no-pager
printf '%s\n' 'ℹ️ Проверка не включает сканирование полной истории Git и внешнее проникновение.'
