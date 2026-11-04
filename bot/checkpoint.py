"""Crash-safe monotonic Telegram update checkpoint.

Persist *before* handling an update: repeated actions won't be run after a crash.
This is at-most-once dispatch, so a crash between the checkpoint and handling
may lose an update. It is not an exactly-once delivery guarantee.
"""
import json
import os
from pathlib import Path
import tempfile


class CheckpointError(Exception):
    """Checkpoint cannot be read or safely persisted."""


class UpdateCheckpoint:
    def __init__(self, filename: Path):
        self.filename = Path(filename)
        self.last_id = self._read()

    def _read(self):
        try:
            if not self.filename.exists():
                return -1
            if self.filename.is_symlink():
                raise CheckpointError("Файл позиции Telegram является ссылкой")
            obj = json.loads(self.filename.read_text(encoding="utf-8"))
            value = obj.get("last_update_id") if isinstance(obj, dict) else None
            if type(value) is not int or value < -1:
                raise ValueError("bad update id")
            return value
        except (OSError, ValueError, UnicodeError) as exc:
            raise CheckpointError("Повреждён или недоступен файл позиции Telegram") from None

    def mark(self, update_id: int) -> bool:
        """Return False for duplicate/out-of-order ids, persist newer ids."""
        if type(update_id) is not int or update_id < 0:
            return False
        if update_id <= self.last_id:
            return False
        parent = self.filename.parent
        temporary = None
        try:
            if self.filename.is_symlink():
                raise OSError("unsafe checkpoint path")
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=parent,
                prefix=".update-checkpoint-", delete=False
            ) as fp:
                temporary = Path(fp.name)
                os.chmod(fp.name, 0o600)
                json.dump({"last_update_id": update_id}, fp)
                fp.write("\n")
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(temporary, self.filename)
            fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise CheckpointError("Не удалось безопасно сохранить позицию Telegram") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        self.last_id = update_id
        return True
