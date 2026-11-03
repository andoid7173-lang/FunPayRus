"""Local-only plugins; Python code is trusted only after manual installation."""
from dataclasses import dataclass
import importlib.util
import json
import os
import re
from pathlib import Path

_CMD = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_RESERVED = {"start", "help", "menu", "status", "plugins", "ping"}

@dataclass(frozen=True)
class PluginInfo:
    name: str
    version: str
    command: str
    description: str

class PluginManager:
    def __init__(self, base_dir: Path, extra_dir: Path | None = None, state_file: Path | None = None):
        self.base_dirs = [Path(base_dir)] + ([Path(extra_dir)] if extra_dir is not None else [])
        self.state_file = Path(state_file) if state_file is not None else None
        self.state = self._read_state()
        self.commands = {}
        self.available = {}
        self.errors = []
        self.load()

    def _read_state(self):
        if not self.state_file or not self.state_file.is_file():
            return {}
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            return {k: v for k, v in raw.items() if _CMD.fullmatch(k) and type(v) is bool} if isinstance(raw, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def set_enabled(self, command, enabled):
        if command not in self.available or type(enabled) is not bool:
            return False
        previous = self.state.copy()
        self.state[command] = enabled
        if self.state_file:
            try:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                temp = self.state_file.with_name(self.state_file.name + ".tmp")
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False)
                os.replace(temp, self.state_file)
            except OSError:
                self.state = previous
                return False
        self.load()
        return True

    def load(self):
        self.commands = {}
        self.available = {}
        self.errors = []
        for base in self.base_dirs:
            if not base.is_dir():
                continue
            for folder in sorted(base.iterdir()):
                if not folder.is_dir() or folder.is_symlink():
                    continue
                manifest_file, source_file = folder / "manifest.json", folder / "plugin.py"
                if not manifest_file.is_file() or not source_file.is_file() or source_file.is_symlink():
                    continue
                try:
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    name = str(data["command"])
                    if not _CMD.fullmatch(name) or name in _RESERVED or name in self.available:
                        raise ValueError("Invalid command")
                    info = PluginInfo(str(data["name"])[:70], str(data.get("version", "0"))[:20],
                                      name, str(data.get("description", ""))[:140])
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
