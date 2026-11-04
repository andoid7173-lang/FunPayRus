#!/usr/bin/env bash
# Reset only FunPay Rust local application state and run its installer again.
set -Eeuo pipefail

APP_DIR=/opt/funpayrus
CONFIG_DIR=/etc/funpayrus
DATA_DIR=/var/lib/funpayrus
SERVICE=funpayrus.service
CONFIRM_PHRASE='RESET FUNPAYRUST'

usage() {
  cat <<'HELP'
FunPay Rust — тест чистой установки на этом VPS.
Использование:
  sudo bash scripts/reset_fresh.sh --dry-run   # показать, что будет удалено
  sudo bash scripts/reset_fresh.sh             # стереть данные после подтверждения

Удаляет ВСЁ содержимое /etc/funpayrus и /var/lib/funpayrus:
токены Telegram/FunPay, роли, настройки, историю заказов и локальные плагины.
Не удаляет код /opt/funpayrus, Ubuntu, журналы systemd, предыдущие резервные
копии /var/backups/funpayrus и информацию на стороне Telegram/FunPay.
После удаления запустит install.sh для новой настройки Bot Token и Admin ID.
ОПЕРАЦИЯ НЕОБРАТИМА ДЛЯ УДАЛЯЕМЫХ ДАННЫХ. РЕЗЕРВНАЯ КОПИЯ НЕ СОЗДАЁТСЯ.
HELP
}

die() { printf '❌ %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  --dry-run) DRY_RUN=1 ;;
  '') DRY_RUN=0 ;;
  *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }

[[ ! -L "$APP_DIR" && -d "$APP_DIR" && -f "$APP_DIR/install.sh" && -f "$APP_DIR/bot/__main__.py" ]] ||
  die 'Проект должен быть установлен в /opt/funpayrus и не быть символической ссылкой.'
for dir in "$CONFIG_DIR" "$DATA_DIR"; do
  [[ ! -L "$dir" ]] || die "Небезопасный каталог (символическая ссылка): $dir"
  [[ ! -e "$dir" || -d "$dir" ]] || die "Ожидался каталог: $dir"
done

# Reject mounts inside the app-only directories before any recursive deletion.
if command -v findmnt >/dev/null 2>&1; then
  while IFS= read -r mount_target; do
    case "$mount_target" in
      "$CONFIG_DIR"|"$CONFIG_DIR"/*|"$DATA_DIR"|"$DATA_DIR"/*)
        die "Обнаружено подключённое устройство/каталог $mount_target. Сброс остановлен." ;;
    esac
  done < <(findmnt -rn -o TARGET)
else
  die 'Для безопасной проверки подключённых каталогов нужен findmnt (util-linux).'
fi

printf '\n🐱 FunPay Rust — СБРОС ДО ПЕРВОЙ УСТАНОВКИ\n'
printf 'Будет полностью удалено содержимое:\n  %s\n  %s\n' "$CONFIG_DIR" "$DATA_DIR"
printf 'Служба %s будет остановлена, затем установщик запустится снова.\n' "$SERVICE"
printf 'Код в %s останется без изменений.\n' "$APP_DIR"
printf 'Старые резервные копии, журналы и история чата Telegram НЕ удаляются.\n\n'
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo '✅ Режим просмотра: ничего не удалено и не остановлено.'
  exit 0
fi

[[ "$EUID" -eq 0 ]] || die 'Нужны root-права: sudo bash scripts/reset_fresh.sh'
[[ -d /run/systemd/system ]] || die 'Нужен работающий systemd на VPS.'
command -v python3 >/dev/null 2>&1 || die 'Для повторной установки нужен python3.'
command -v git >/dev/null 2>&1 || die 'Для повторной установки нужен git.'
[[ -r /dev/tty ]] || die 'Подтверждение и повторный ввод токена требуют интерактивного терминала.'

printf '⚠️  ВНИМАНИЕ: данные FunPay Rust будут удалены БЕЗ резервной копии.\n'
printf 'Для подтверждения введите точно: %s\n' "$CONFIRM_PHRASE"
if ! IFS= read -r -p '> ' confirmation </dev/tty; then
  die 'Нет интерактивного подтверждения — ничего не удалено.'
fi
[[ "$confirmation" == "$CONFIRM_PHRASE" ]] || { echo 'Отменено. Ничего не удалено.'; exit 0; }
unset confirmation

# Stop the application first, so SQLite and other state files are not in use.
if systemctl cat "$SERVICE" >/dev/null 2>&1; then
  systemctl stop "$SERVICE" || die 'Не удалось остановить службу. Данные НЕ удалены.'
  if systemctl is-active --quiet "$SERVICE"; then
    die 'Служба всё ещё активна. Данные НЕ удалены.'
  fi
fi

# Bash glob does NOT include . or ..; dotglob includes hidden app files.
shopt -s nullglob dotglob
for dir in "$CONFIG_DIR" "$DATA_DIR"; do
  if [[ -d "$dir" ]]; then
    entries=("$dir"/*)
    if ((${#entries[@]})); then
      rm -rf -- "${entries[@]}"
    fi
  fi
done
shopt -u nullglob dotglob

echo '✅ Локальные настройки и история FunPay Rust сброшены.'
echo '🔐 Запускаю установку заново. Подготовьте Telegram Bot Token и свой числовой ID.'
cd "$APP_DIR"
bash install.sh
