import json
from pathlib import Path
from typing import Any

from src.config import settings
from src.privacy import sanitize_for_memory


MAX_DIALOG_PAIRS = 10


class DialogMemory:
    def __init__(
        self,
        path: Path = settings.dialog_context_file,
        max_pairs: int = MAX_DIALOG_PAIRS,
    ) -> None:
        self.path = path
        self.max_pairs = max_pairs
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _read(self) -> dict[str, list[dict[str, str]]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

        if not isinstance(data, dict):
            return {}
        return {
            str(user_id): messages
            for user_id, messages in data.items()
            if isinstance(messages, list)
        }

    def _write(self, data: dict[str, list[dict[str, str]]]) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_messages(self, user_id: int | str | None) -> list[dict[str, str]]:
        if user_id is None:
            return []
        return self._read().get(str(user_id), [])[-self.max_pairs * 2 :]

    def format_context(self, user_id: int | str | None) -> str:
        messages = self.get_messages(user_id)
        if not messages:
            return ""

        lines: list[str] = []
        for message in messages:
            role = "Клиент" if message.get("role") == "user" else "Ассистент"
            lines.append(f"{role}: {message.get('content', '')}")
        return "\n".join(lines)

    def add_pair(
        self,
        user_id: int | str | None,
        user_text: str,
        assistant_text: str,
    ) -> None:
        if user_id is None:
            return

        data = self._read()
        key = str(user_id)
        messages = data.get(key, [])
        messages.extend(
            [
                {"role": "user", "content": sanitize_for_memory(user_text)},
                {"role": "assistant", "content": sanitize_for_memory(assistant_text)},
            ]
        )
        data[key] = messages[-self.max_pairs * 2 :]
        self._write(data)

    def clear(self, user_id: int | str | None = None) -> int:
        data = self._read()
        if user_id is None:
            count = sum(len(messages) for messages in data.values())
            self._write({})
            return count

        key = str(user_id)
        count = len(data.get(key, []))
        data.pop(key, None)
        self._write(data)
        return count


dialog_memory = DialogMemory()
