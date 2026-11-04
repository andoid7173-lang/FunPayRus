"""Local-only plugins. External code must be manually reviewed before enabling."""
from dataclasses import dataclass
import importlib.util
import json
import os
import re
from pathlib import Path
import tempfile

_CMD = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_RESERVED = {"start", "help", "menu", "status", "plugins", "ping", "funpay"}


@dataclass(frozen=True)
class PluginInfo:
    name: str
    version: str
    command: str
    description: str


class PluginStateError(Exception):
    """Refuse to load plugins when persisted enable/disable state is unsafe."""


class PluginManager:
    def __init__(self, base_dir: Path, extra_dir: Path | None = None,
                 state_file: Path | None = None, *, allow_external=False):
        self.base_dirs = [Path(base_dir)] + ([Path(extra_dir)] if extra_dir is not None else [])
        self.state_file = Path(state_file) if state_file is not None else None
        self.allow_external = allow_external
        self.state = self._read_state()
        self.commands = {}
        self.available = {}
        self.errors = []
        self.load()

    def _read_state(self):
        if not self.state_file:
            return {}
        try:
            # A dangling symlink is not an absent state file.
            if self.state_file.is_symlink():
                raise PluginStateError("Состояние плагинов не должно быть ссылкой")
            if not self.state_file.exists():
                return {}
            if not self.state_file.is_file():
                raise PluginStateError("Некорректный путь файла состояния плагинов")
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or any(
                not isinstance(k, str) or not _CMD.fullmatch(k) or type(v) is not bool
                for k, v in raw.items()
            ):
                raise PluginStateError("Повреждён файл состояния плагинов")
            return raw
        except (OSError, ValueError, UnicodeError) as exc:
            raise PluginStateError("Невозможно безопасно прочитать состояние плагинов") from None

    def set_enabled(self, command, enabled):
        if command not in self.available or type(enabled) is not bool:
            return False
        previous = self.state.copy()
        self.state[command] = enabled
        if self.state_file:
            tmp = None
            try:
                if self.state_file.is_symlink():
                    raise OSError("unsafe state file")
                self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.state_file.parent,
                    prefix=".plugin-state-", delete=False
                ) as fp:
                    tmp = Path(fp.name)
                    os.chmod(fp.name, 0o600)
                    json.dump(self.state, fp, ensure_ascii=False)
                    fp.write("\n")
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp, self.state_file)
            except OSError:
                self.state = previous
                return False
            finally:
                if tmp is not None:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
        self.load()
        return True

    def load(self):
        self.commands = {}
        self.available = {}
        self.errors = []
        for index, base in enumerate(self.base_dirs):
            # Built-in plugin directory is part of a trusted git checkout.
            # External plugins are disabled in the systemd runtime by default.
            if index > 0 and not self.allow_external:
                self.errors.append("Сторонние плагины отключены политикой безопасности")
                continue
            if not base.is_dir() or base.is_symlink():
                continue
            for folder in sorted(base.iterdir()):
                if not folder.is_dir() or folder.is_symlink():
                    continue
                manifest_file, source_file = folder / "manifest.json", folder / "plugin.py"
                if (not manifest_file.is_file() or manifest_file.is_symlink()
                        or not source_file.is_file() or source_file.is_symlink()):
                    continue
                try:
                    if manifest_file.stat().st_size > 16_384:
                        raise ValueError("Manifest too large")
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("Invalid manifest")
                    name = str(data["command"])
                    if not _CMD.fullmatch(name) or name in _RESERVED or name in self.available:
                        raise ValueError("Invalid command")
                    info = PluginInfo(
                        str(data["name"]).replace("\n", " ").replace("\r", " ")[:70],
                        str(data.get("version", "0"))[:20], name,
                        str(data.get("description", "")).replace("\n", " ")[:140]
                    )
                    self.available[name] = info
                    if not self.state.get(name, data.get("enabled") is True):
                        continue
                    spec = importlib.util.spec_from_file_location("fpr_" + folder.name, source_file)
                    if spec is None or spec.loader is None:
                        raise ValueError("Invalid plugin")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    handler = getattr(module, "handle", None)
                    if not callable(handler):
                        raise ValueError("Missing handle")
                    self.commands[name] = (info, handler)
                except Exception as exc:
                    self.errors.append(f"{folder.name}: {type(exc).__name__}")

    def list_plugins(self):
        return [info for info, _ in self.commands.values()]

    def list_all_plugins(self):
        return [(info, name in self.commands) for name, info in self.available.items()]

    def execute(self, command, args):
        item = self.commands.get(command)
        if not item:
            return None
        try:
            return str(item[1](args[:500]))[:3500]
        except Exception:
            return "❌ Ошибка плагина. Проверьте журнал службы."
