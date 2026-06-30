import json
from pathlib import Path
from typing import Any

from src.config import settings


class JsonCache:
    def __init__(self, path: Path = settings.cache_file) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key: str) -> str | None:
        value = self._read().get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def clear(self) -> int:
        data = self._read()
        count = len(data)
        self._write({})
        return count


cache = JsonCache()
