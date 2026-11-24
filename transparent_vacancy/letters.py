"""Дополнительный сценарий: черновик запроса работодателю по замечаниям отчёта.

Письмо собирается только из того, что уже есть в отчёте: названия должности,
работодателя, цитат из вакансии и рекомендаций. Новых фактов оно не добавляет.
"""

from __future__ import annotations

import re

# Рекомендации написаны для редактора («Уточните у работодателя…»). В письме адресат — сам работодатель.
_TO_EMPLOYER = (
    (re.compile(r"^Уточнит[еь] у работодателя,?\s*", re.I), "Уточните, пожалуйста, "),
    (re.compile(r"^Попросите работодателя указать\s*", re.I), "Укажите, пожалуйста, "),
    (re.compile(r"^Попросите работодателя перечислить\s*", re.I), "Перечислите, пожалуйста, "),
    (re.compile(r"^Попросите работодателя привести\s*", re.I), "Приведите, пожалуйста, "),
    (re.compile(r"^Уточнит[еь],?\s*", re.I), "Уточните, пожалуйста, "),
)


def _request(recommendation: str) -> str:
    for pattern, replacement in _TO_EMPLOYER:
        if pattern.search(recommendation):
            return pattern.sub(replacement, recommendation, count=1)
    return recommendation


def _stated(finding: dict) -> str:
    """Что сейчас написано в вакансии по этому пункту."""
    if finding["type"] == "contradiction" and len(finding.get("conflict") or []) == 2:
        first, second = finding["conflict"]
        return (
            f"Сейчас в вакансии два разных значения: «{first['value']}» (поле {first['field']}) "
            f"и «{second['value']}» (поле {second['field']})."
        )
    if finding["quote"]:
        return f"Сейчас в вакансии указано: «{finding['quote']}» (поле {finding['field']})."
    return f"Сейчас в вакансии этих сведений нет (поле {finding['field']})."


def build_letter(entry: dict) -> str | None:
    """Текст запроса по одной записи отчёта. ``None``, если уточнять нечего."""
    findings = entry.get("findings") or []
    if not findings:
        return None
    title = entry.get("job_name") or "без названия"
    lines = [
        f"# Запрос на уточнение условий вакансии «{title}»",
        "",
        f"Кому: {entry.get('company') or 'работодателю'}",
    ]
    if entry.get("url"):
        lines.append(f"Вакансия: {entry['url']}")
    lines += [
        f"ID вакансии: {entry.get('id') or 'не указан'}",
        "",
        "Здравствуйте!",
        "",
        f"При проверке вакансии «{title}» остались вопросы к описанию условий работы. "
        "Чтобы соискателям было понятно, на что они откликаются, просим уточнить:",
        "",
    ]
    for number, finding in enumerate(findings, 1):
        lines.append(f"{number}. **{finding['topic_title']}.** {_stated(finding)} {_request(finding['recommendation'])}")
    lines += ["", "После вашего ответа мы обновим описание вакансии.", ""]
    return "\n".join(lines)


def build_letters(report: dict) -> dict[str, str]:
    """Письма по всем вакансиям отчёта с замечаниями: имя файла → текст."""
    letters: dict[str, str] = {}
    for position, entry in enumerate(report.get("vacancies") or [], 1):
        text = build_letter(entry)
        if text is None:
            continue
        name = re.sub(r"[^0-9A-Za-z_-]+", "_", str(entry.get("id") or f"vacancy-{position}")).strip("_")
        letters[f"{name or f'vacancy-{position}'}.md"] = text
    return letters
