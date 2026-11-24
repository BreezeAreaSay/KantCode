"""Файлы результата: текущий отчёт, статус последнего запуска и кэш разборов.

Отчёт заменяется одним переименованием и только после успешного запуска, поэтому
при сбое источника на диске остаётся прошлый успешный отчёт.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPORT_NAME = "report.json"
STATUS_NAME = "last_run.json"
CACHE_NAME = "cache.json"
CACHE_LIMIT = 5000


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Cache:
    """Разбор вакансии по хешу её содержимого.

    Неизменённая вакансия не разбирается заново: повторный запуск даёт тот же
    результат и не тратит запросы к модели.
    """

    def __init__(self, path: Path | None):
        self.path = path
        data = read_json(path, {}) if path else {}
        self.entries: dict[str, dict] = data if isinstance(data, dict) else {}
        self.changed = False

    def get(self, key: str) -> dict | None:
        entry = self.entries.get(key)
        return entry if isinstance(entry, dict) else None

    def put(self, key: str, entry: dict) -> None:
        self.entries.pop(key, None)
        self.entries[key] = entry
        self.changed = True

    def save(self) -> None:
        if not self.path or not self.changed:
            return
        while len(self.entries) > CACHE_LIMIT:
            self.entries.pop(next(iter(self.entries)))
        write_json(self.path, self.entries)
        self.changed = False
