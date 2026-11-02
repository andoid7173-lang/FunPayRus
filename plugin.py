"""Example plugin. Third-party plugins execute code: only install trusted authors."""

def handle(args: str) -> str:
    return "🐱 Привет из плагина FunPay Rus!" if not args else f"🐱 Привет! Сообщение: {args[:200]}"
