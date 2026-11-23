"""VPS-only Microsoft OAuth, using an encrypted persistent MSAL token cache.

Only a user's own delegated Mail.ReadBasic permission is requested. The
interactive device code stays in the SSH terminal, never in Telegram.
"""
from pathlib import Path
import os
import tempfile

SCOPES = ["Mail.ReadBasic"]
AUTHORITY = "https://login.microsoftonline.com/common"
STATE_DIR = Path(os.environ.get("FUNPAYRUS_STATE_DIR", "/var/lib/funpayrus"))
KEY_FILE = Path(os.environ.get("FUNPAYRUS_MAIL_KEY_FILE", "/etc/funpayrus/mail.key"))
CACHE_FILE = "mail-msal-cache.enc"


class MailAuthError(Exception):
    pass


def _fernet(key_path=KEY_FILE):
    from cryptography.fernet import Fernet
    path = Path(key_path)
    if (path.is_symlink() or not path.is_file() or
            path.stat().st_mode & 0o007 or path.stat().st_size != 44):
        raise MailAuthError("Ключ почты отсутствует или недостаточно защищён")
    try:
        return Fernet(path.read_bytes())
    except (OSError, ValueError):
        raise MailAuthError("Не удалось прочитать ключ почты") from None


def _paths(directory=STATE_DIR):
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise MailAuthError("Не найден защищённый каталог данных")
    path = directory / CACHE_FILE
    if path.is_symlink():
        raise MailAuthError("Файл токенов не должен быть символьной ссылкой")
    return path


def load_app(client_id, *, directory=STATE_DIR, key_path=KEY_FILE):
    """Return (PublicClientApplication, cache, path, cipher) with preserved RT."""
    if not isinstance(client_id, str) or not 8 <= len(client_id) <= 100:
        raise MailAuthError("MAIL_CLIENT_ID не настроен")
    try:
        import msal
        from cryptography.fernet import InvalidToken
    except ImportError:
        raise MailAuthError("Для Outlook установи зависимости requirements-mail.txt") from None
    path = _paths(directory)
    cipher = _fernet(key_path)
    cache = msal.SerializableTokenCache()
    if path.is_file():
        if (path.stat().st_mode & 0o077 or path.stat().st_size > 1_000_000):
            raise MailAuthError("Небезопасные права или размер кэша почты")
        try:
            cache.deserialize(cipher.decrypt(path.read_bytes()).decode("utf-8"))
        except (InvalidToken, UnicodeError, ValueError, OSError):
            raise MailAuthError("Кэш авторизации повреждён: требуется проверка вручную") from None
    app = msal.PublicClientApplication(client_id, authority=AUTHORITY, token_cache=cache)
    return app, cache, path, cipher


def persist_cache(cache, path, cipher):
    """Atomic 0600 replacement, never write decrypted MSAL data to disk."""
    path = Path(path)
    if path.is_symlink():
        raise MailAuthError("Небезопасный путь кэша")
    blob = cipher.encrypt(cache.serialize().encode("utf-8"))
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".mail-cache-",
                                         delete=False) as fp:
            tmp = Path(fp.name)
            os.fchmod(fp.fileno(), 0o600)
            fp.write(blob)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError:
        raise MailAuthError("Не удалось сохранить авторизацию") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def access_token(client_id, *, directory=STATE_DIR, key_path=KEY_FILE):
    """Return a valid token; MSAL refreshes from its persistent cache if needed."""
    app, cache, path, cipher = load_app(client_id, directory=directory, key_path=key_path)
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if cache.has_state_changed:
        persist_cache(cache, path, cipher)
    if not result or "access_token" not in result:
        return None
    return result["access_token"]


def connected(client_id, *, directory=STATE_DIR, key_path=KEY_FILE):
    # Do not call Graph or refresh in a Telegram status command.
    app, _, _, _ = load_app(client_id, directory=directory, key_path=key_path)
    return bool(app.get_accounts())


def generate_key(path=KEY_FILE):
    """Install once as root, without replacing any pre-existing path."""
    from cryptography.fernet import Fernet
    path = Path(path)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, "wb") as fp:
        fp.write(Fernet.generate_key())
        fp.flush()
        os.fsync(fp.fileno())


def configured_client_id():
    """Read the public app ID from the protected service environment file."""
    val = os.environ.get("MAIL_CLIENT_ID", "")
    if val:
        return val
    path = Path("/etc/funpayrus/bot.env")
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MAIL_CLIENT_ID="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return ""
    return ""


def cli(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Outlook OAuth только в SSH-терминале")
    parser.add_argument("action", choices=("connect", "disconnect", "status"))
    parser.add_argument("--client-id", default=configured_client_id())
    parser.add_argument("--state-dir", default=str(STATE_DIR))
    parser.add_argument("--key-file", default=str(KEY_FILE))
    args = parser.parse_args(argv)
    if not os.isatty(0) or not os.isatty(1):
        parser.error("Для операций с OAuth нужен интерактивный SSH-терминал")
    try:
        if args.action == "disconnect":
            path = _paths(args.state_dir)
            path.unlink(missing_ok=True)
            print("Локальный кэш удалён. Дополнительно отзови согласие Microsoft.")
            return 0
        app, cache, path, cipher = load_app(
            args.client_id, directory=args.state_dir, key_path=args.key_file)
        if args.action == "status":
            print("Подключено (кэш)" if app.get_accounts() else "Не подключено")
            return 0
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise MailAuthError("Microsoft отклонил запуск авторизации")
        # Show only in SSH, and only when the owner invoked the command.
        print("В своём браузере открой официальный адрес Microsoft:")
        print(flow["verification_uri"])
        print("Введи код из своего терминала:", flow["user_code"])
        print("Сверь название приложения и доступ Mail.ReadBasic.")
        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise MailAuthError("Подключение не завершено или отклонено")
        persist_cache(cache, path, cipher)
        print("Подключение сохранено. Перезапусти funpayrus.")
        return 0
    except (MailAuthError, OSError) as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
