"""Очистка и поиск текста: общие мелочи для правил и проверки ответов ИИ."""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"</?[a-zA-Z][^<>]*>")
_SPACES = re.compile(r"\s+")

# Значения, которыми формы портала заполняют «ничего не указано».
PLACEHOLDERS = {
    "",
    "-",
    "—",
    "–",
    ".",
    "нет",
    "не указано",
    "не указан",
    "не указана",
    "ничего не выбрано",
    "null",
    "none",
    "н/д",
    "n/a",
}


def clean_text(value) -> str:
    """Строка без HTML-разметки и лишних пробелов. Не строки приводятся к строке."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if not isinstance(value, str):
        value = str(value)
    text = html.unescape(value)
    text = _TAG.sub(" ", text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    return _SPACES.sub(" ", text).strip()


def is_placeholder(text: str) -> bool:
    return text.strip().lower() in PLACEHOLDERS


def fold(text: str) -> str:
    """Форма для сравнения: нижний регистр, «ё» как «е», одинарные пробелы."""
    return _SPACES.sub(" ", text.lower().replace("ё", "е")).strip()


def find_quote(haystack: str, quote: str) -> str:
    """Возвращает фрагмент haystack, совпадающий с quote без учёта регистра и пробелов.

    Пустая строка значит, что такой цитаты в тексте нет: её придумали.
    """
    needle = fold(quote).strip(" .,;:!?\"'«»()")
    if not needle or not haystack:
        return ""
    # fold() не меняет длину строки, пока пробелы уже одинарные: clean_text это гарантирует.
    folded = haystack.lower().replace("ё", "е")
    start = folded.find(needle)
    if start < 0:
        return ""
    return haystack[start : start + len(needle)]


def excerpt(text: str, start: int, end: int, limit: int = 160) -> str:
    """Фрагмент text[start:end], расширенный до границ слов и обрезанный по limit."""
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    piece = text[start:end].strip(" ,;:")
    if len(piece) > limit:
        piece = piece[:limit].rsplit(" ", 1)[0]
    return piece
