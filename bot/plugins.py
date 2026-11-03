"""Loads ONLY locally installed plugins. Do not install untrusted Python modules."""
from dataclasses import dataclass
import importlib.util
import json
import re
from pathlib import Path

_CMD = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_RESERVED = {"start", "help", "status", "plugins", "ping"}

@dataclass(frozen=True)
class PluginInfo:
    name: str
    version: str
    command: str
    description: str


class PluginManager:
    def __init__(self, base_dir: Path, extra_dir: Path | None = None):
        self.base_dirs = [Path(base_dir)] + ([Path(extra_dir)] if extra_dir is not None else [])
        self.commands: dict[str, tuple[PluginInfo, object]] = {}
        self.errors: list[str] = []
        self.load()

    def load(self):
        self.commands.clear()
        self.errors.clear()
        folders = []
        for base in self.base_dirs:
            if base.is_dir():
                folders.extend(sorted(base.iterdir()))
        for folder in folders:
            if not folder.is_dir() or folder.is_symlink():
                continue
            manifest_path = folder / "manifest.json"
            file_path = folder / "plugin.py"
            if not manifest_path.is_file() or not file_path.is_file() or file_path.is_symlink():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("enabled") is not True:
                    continue
                command = str(manifest["command"])
                if not _CMD.fullmatch(command) or command in _RESERVED or command in self.commands:
                    raise ValueError("Недопустимая или повторяющаяся команда")
                info = PluginInfo(
                    name=str(manifest["name"])[:70],
                    version=str(manifest.get("version", "0"))[:20],
                    command=command,
                    description=str(manifest.get("description", ""))[:140],
                )
                spec = importlib.util.spec_from_file_location("fpr_plugin_" + folder.name, file_path)
                if spec is None or spec.loader is None:
                    raise ValueError("Не удалось загрузить Python-файл")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                handler = getattr(module, "handle", None)
                if not callable(handler):
                    raise ValueError("Плагин должен содержать handle(args: str) -> str")
                self.commands[command] = (info, handler)
            except Exception as exc:
                # Don't print plugin contents or environment secrets.
                self.errors.append(f"{folder.name}: {type(exc).__name__}")

    def list_plugins(self):
        return [info for info, _ in self.commands.values()]

    def execute(self, command: str, args: str):
        value = self.commands.get(command)
        if not value:
            return None
        _, handler = value
        try:
            result = handler(args[:500])
            return str(result)[:3500]
        except Exception:
            return "❌ Плагин завершился с ошибкой. Проверьте журнал службы."
