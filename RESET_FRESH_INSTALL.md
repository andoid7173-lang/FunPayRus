# FunPay Rust — сброс перед тестом установки

Для тестового Ubuntu/Debian VPS. **Операция необратимо удаляет локальные данные приложения.**

Предпросмотр без удаления:

```bash
cd /opt/funpayrus
sudo bash scripts/reset_fresh.sh --dry-run
```

Сброс с подтверждением и повторная настройка Telegram:

```bash
cd /opt/funpayrus
sudo bash scripts/reset_fresh.sh
```

Для продолжения нужно вручную ввести точную фразу `RESET FUNPAYRUST`. После остановки `funpayrus.service` удаляется **всё содержимое** `/etc/funpayrus` и `/var/lib/funpayrus`: Telegram Bot Token, Golden Key, локальная история заказов, администраторы, настройки, плагины и Telegram offset. Затем вызывается `install.sh`, который снова просит Bot Token и Admin ID.

**Не удаляется:** Ubuntu, SSH, Git-репозиторий `/opt/funpayrus`, журнал systemd, резервные копии в `/var/backups/funpayrus`, данные аккаунтов FunPay/Telegram и старые сообщения Telegram.

**Резервная копия не создаётся**. Сохраните нужные данные заранее. Если ввод нового токена прерван, можно повторить `cd /opt/funpayrus && sudo bash install.sh`.

Сброс не гарантирует очистки снапшотов VPS и не является криптографически безопасным стиранием диска. Для полной имитации нового пользователя создайте нового Telegram-бота через @BotFather: старый чат в Telegram скрипт очистить не может.
