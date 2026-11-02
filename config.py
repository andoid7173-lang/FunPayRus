"""Only reads variables provided by the systemd EnvironmentFile."""
import os
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"^[0-9]{5,15}:[A-Za-z0-9_-]{30,}$")

@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_id: int

    @classmethod
    def load(cls, env=None):
        env = os.environ if env is None else env
        token = str(env.get("BOT_TOKEN", "")).strip()
        admin = str(env.get("ADMIN_ID", "")).strip()
        if not _TOKEN.fullmatch(token):
            raise ValueError("Неверный формат BOT_TOKEN; настройте токен через установщик.")
        if not admin.isascii() or not admin.isdecimal() or int(admin) <= 0:
            raise ValueError("ADMIN_ID должен быть положительным числом.")
        return cls(bot_token=token, admin_id=int(admin))
