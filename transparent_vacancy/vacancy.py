"""Вакансия в формате API «Работа в России»: разбор входных данных и доступ к полям.

Имена полей в отчёте совпадают с путями в JSON источника (``job-name``,
``requirement.experience``, ``addresses.address[0].location``), чтобы редактор
нашёл поле в исходной записи без таблицы соответствия.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .textutil import clean_text, is_placeholder

# Поля, которые читают правила и ИИ. Порядок сохраняется в запросе к модели.
TEXT_FIELDS = (
    "job-name",
    "duty",
    "requirements",
    "qualification",
    "requirement.qualification",
    "requirement.education",
    "requirement.experience",
    "salary",
    "salary_min",
    "salary_max",
    "term.text",
    "schedule",
    "shift",
    "scheduleTypeComment",
    "employment",
    "conditions",
    "benefit",
    "benefitDetails",
    "skills",
    "addressOffice",
)

_SALARY_BOUNDS = ("salary_min", "salary_max")

# Поля свободного текста: в них ищем упоминания графика, оплаты, опыта.
FREE_TEXT_FIELDS = (
    "duty",
    "requirements",
    "qualification",
    "requirement.qualification",
    "term.text",
    "scheduleTypeComment",
    "benefitDetails",
)


def _dig(raw: dict, path: str) -> Any:
    node: Any = raw
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        digits = value.replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            return float(digits)
        except ValueError:
            return None
    return None


def _join(value: Any) -> str:
    """Список значений портала одной строкой, без повторов."""
    if isinstance(value, (list, tuple)):
        seen: list[str] = []
        for item in value:
            text = clean_text(item)
            if text and text not in seen:
                seen.append(text)
        return ", ".join(seen)
    return clean_text(value)


class Vacancy:
    """Одна вакансия источника и её поля в очищенном виде."""

    def __init__(self, raw: dict, index: int = 0):
        self.raw = raw
        self.index = index
        self.fields: dict[str, str] = {}
        for path in TEXT_FIELDS:
            value = _dig(raw, path)
            if path == "term.text" and value is None and isinstance(raw.get("term"), str):
                value = raw.get("term")
            if path in _SALARY_BOUNDS and _as_number(value) == 0:
                continue  # ноль в границе зарплаты значит «не указано», а не сумму
            text = _join(value)
            if text and not is_placeholder(text):
                self.fields[path] = text
        for position, location in enumerate(self._locations()):
            self.fields[f"addresses.address[{position}].location"] = location

    # --- идентификация -------------------------------------------------

    @property
    def id(self) -> str | None:
        value = self.raw.get("id")
        if value is None or clean_text(value) == "":
            return None
        return clean_text(value)

    @property
    def url(self) -> str | None:
        return clean_text(self.raw.get("vac_url")) or None

    @property
    def title(self) -> str:
        return self.fields.get("job-name", "")

    @property
    def company(self) -> str:
        return clean_text(_dig(self.raw, "company.name"))

    @property
    def modified(self) -> str:
        return clean_text(self.raw.get("date_modify")) or clean_text(self.raw.get("creation-date"))

    def fingerprint(self) -> str:
        """Хеш содержимого: по нему видно, что вакансия не менялась с прошлого запуска."""
        body = json.dumps(self.raw, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    # --- поля ----------------------------------------------------------

    def get(self, path: str) -> str:
        return self.fields.get(path, "")

    def number(self, path: str) -> float | None:
        return _as_number(_dig(self.raw, path))

    def _locations(self) -> list[str]:
        addresses = _dig(self.raw, "addresses.address")
        if isinstance(addresses, dict):
            addresses = [addresses]
        if isinstance(addresses, str):
            addresses = [{"location": addresses}]
        if not isinstance(addresses, list):
            addresses = []  # число, флаг или пусто: адреса нет
        result: list[str] = []
        for item in addresses:
            location = clean_text(item.get("location") if isinstance(item, dict) else item)
            if location and not is_placeholder(location):
                result.append(location)
        return result

    @property
    def location_fields(self) -> list[str]:
        return [name for name in self.fields if name.startswith("addresses.address[")]

    def free_text(self) -> list[tuple[str, str]]:
        """Пары (поле, текст) свободного описания, в порядке важности."""
        return [(name, self.fields[name]) for name in FREE_TEXT_FIELDS if name in self.fields]


def _looks_like_vacancy(node: Any) -> bool:
    return isinstance(node, dict) and any(
        key in node for key in ("job-name", "duty", "vac_url", "salary_min", "requirement", "company")
    )


def extract_vacancies(payload: Any) -> list[dict]:
    """Достаёт вакансии из ответа API в любом виде, в каком его могут передать.

    Понимает ответ списка (``results.vacancies[].vacancy``), ответ одной вакансии,
    массив таких ответов (несколько страниц в одном файле) и голый массив вакансий.
    """
    found: list[dict] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("vacancy"), dict):
            found.append(node["vacancy"])
            return
        results = node.get("results")
        if isinstance(results, dict):
            visit(results.get("vacancies"))
            return
        if isinstance(results, list):
            visit(results)
            return
        if "vacancies" in node:
            visit(node.get("vacancies"))
            return
        if _looks_like_vacancy(node):
            found.append(node)

    visit(payload)
    return found


def deduplicate(vacancies: Iterable[Vacancy]) -> tuple[list[Vacancy], int]:
    """Оставляет по одной вакансии на ID: последнюю по дате изменения.

    Вакансии без ID не склеиваются: у каждой свой объект в отчёте.
    """
    by_id: dict[str, Vacancy] = {}
    order: list[Vacancy] = []
    duplicates = 0
    for vacancy in vacancies:
        key = vacancy.id
        if key is None:
            order.append(vacancy)
            continue
        known = by_id.get(key)
        if known is None:
            by_id[key] = vacancy
            order.append(vacancy)
            continue
        duplicates += 1
        if vacancy.modified >= known.modified:
            order[order.index(known)] = vacancy
            by_id[key] = vacancy
    return order, duplicates
