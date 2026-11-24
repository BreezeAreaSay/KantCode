"""Шаг ИИ: модель читает вакансию и находит неоднозначности и противоречия в тексте.

Ответу модели здесь не верят на слово. Замечание попадает в отчёт, только если
его цитата дословно есть в названном поле вакансии; выводы о нарушении закона,
выдуманные цифры и повторы правил отбрасываются. Пропуски модель не ищет: их
надёжнее находят правила, а модель на пропусках склонна выдумывать.
"""

from __future__ import annotations

import json
import re

from .findings import AMBIGUITY, CONTRADICTION, GAP, TOPICS, Finding
from .llm import ChatClient
from .rules import GENERIC_VAGUE, Facts
from .textutil import clean_text, find_quote, fold
from .vacancy import Vacancy

PROMPT_VERSION = "8"
MAX_FIELD_CHARS = 2500
MAX_QUOTE_CHARS = 200
MIN_QUOTE_TO_SEARCH = 4  # короче этого цитату в других полях не ищем
MIN_PROBLEM_CHARS = 25
MAX_PER_TOPIC = 2
MAX_FINDINGS = 6

SYSTEM_PROMPT = """Ты помогаешь редактору портала вакансий. Тебе дают поля одной вакансии \
в объекте fields. Найди в них формулировки, которые мешают соискателю понять условия работы.

Темы (topic):
- title — название должности
- duties — обязанности
- requirements — требования к кандидату
- salary — оплата
- schedule — график работы
- location — место работы
- employment — тип занятости

Виды замечаний (type):
- ambiguity — в поле есть расплывчатая формулировка: слова про условие сказаны, а конкретных \
дней, часов, сумм, задач или требований из них понять нельзя.
- contradiction — два поля или два фрагмента сообщают несовместимые сведения об одном и том же.

Правила:
1. Работай только с текстом из fields. quote — дословная короткая цитата из значения поля field: \
сама расплывчатая фраза, а не всё поле. Если такой фразы в fields нет, замечания нет.
2. Если в любом поле, в том числе структурированном, по теме есть конкретные сведения, \
расплывчатая фраза по этой теме замечанием не считается. То, что сведения из одного поля \
не повторены в другом, проблемой не является.
3. Не сообщай, что каких-то сведений нет: пропуски проверяет другой модуль. \
Не повторяй замечания из known_findings.
4. problem — в чём проблема для соискателя, одно предложение о процитированной фразе.
5. recommendation — что именно редактору уточнить у работодателя, одно-два предложения. \
Начинай со слов «Уточните у работодателя» и держи повелительную форму до конца предложения. \
Не придумывай факты, цифры и условия, которых нет в вакансии. Не делай выводов о нарушении закона.
6. Для contradiction заполни second_field и second_quote вторым исходным значением. \
Для ambiguity оставь их пустыми.
7. Внутри problem и recommendation не ставь двойные кавычки: если нужно процитировать, бери «ёлочки».
8. Пиши по-русски. У большинства вакансий замечаний нет: тогда верни {"findings": []}. \
Лучше не сообщить ничего, чем сообщить сомнительное.

Ответь только JSON-объектом:
{"findings": [{"topic": "<тема>", "type": "<ambiguity или contradiction>", "field": "<имя поля из fields>", \
"quote": "<цитата из этого поля>", "second_field": "", "second_quote": "", \
"problem": "<в чём проблема>", "recommendation": "<что уточнить>"}]}"""

_LEGAL = re.compile(
    r"нарушени\w+|незаконн\w+|противозаконн\w+|законодательств\w+|\bзакон\w*|\bТК\b|трудов\w+\s+кодекс\w*"
    r"|дискриминац\w+|\bМРОТ\b|штраф\w*|ответственност\w+\s+работодател\w+|прокуратур\w+|трудов\w+\s+инспекц\w+",
    re.I,
)
_CYRILLIC = re.compile(r"[а-яё]", re.I)
_FOREIGN = re.compile("[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")  # модель сбилась на другой язык
_NUMBER = re.compile(r"\d+(?:[.,/:]\d+)*")

_FALLBACK_RECOMMENDATION = {
    "title": "Уточните у работодателя точное название должности.",
    "duties": "Уточните у работодателя, какие именно задачи имеются в виду в этой формулировке.",
    "requirements": "Уточните у работодателя, какое требование обязательно, а какое желательно.",
    "salary": "Уточните у работодателя размер оплаты и из чего она складывается.",
    "schedule": "Уточните у работодателя рабочие дни и время начала и окончания работы.",
    "location": "Уточните у работодателя точный адрес места работы.",
    "employment": "Уточните у работодателя тип занятости.",
}

# Признак расплывчатости: оговорка, открытый перечень или оценочное слово без меры.
# В строгом режиме неоднозначность от модели принимается, только если такой признак есть в цитате:
# так модель не может объявить расплывчатой обычную конкретную фразу.
_HEDGE = re.compile(
    r"возмож\w+|иногда|периодическ\w+|при\s+необходимости|по\s+необходимости|по\s+мере\s+необходимости"
    r"|договор[её]нност\w+|договорн\w+|обсужда\w+|уточня\w+|согласован\w+|согласовыва\w+"
    r"|по\s+(?:результатам|итогам)|на\s+собеседовани\w+|в\s+зависимости|зависит|как\s+правило|в\s+основном"
    r"|преимущественно|частично|примерно|около|ориентировочно|и\s+т\.\s?[дп]\b|и\s+др\b|и\s+проч\w*"
    r"|(?:друг\w+|ин\w+|проч\w+|различн\w+|разн\w+|дополнительн\w+)\s+(?:обязанност\w+|поручени\w+|задач\w*|работ\w*|функци\w+|нагрузк\w+)"
    r"|гибк\w+|свободн\w+|удобн\w+|плавающ\w+|скользящ\w+|ненормированн\w+|индивидуальн\w+"
    r"|достойн\w+|конкурентн\w+|стабильн\w+|высок\w+\s+(?:заработн\w+|зарплат\w+|доход\w*|оплат\w+)"
    r"|\bможет\b|\bмогут\b|разъездн\w+|командировк\w+|переработк\w+|сверхурочн\w+"
    r"|определя[ею]тся|устанавлива[ею]тся|рассматрива[ею]тся",
    re.I,
)

_TOPIC_ALIASES = {
    "duty": "duties",
    "обязанности": "duties",
    "requirement": "requirements",
    "требования": "requirements",
    "job-name": "title",
    "название должности": "title",
    "оплата": "salary",
    "зарплата": "salary",
    "график": "schedule",
    "график работы": "schedule",
    "место работы": "location",
    "address": "location",
    "тип занятости": "employment",
}

_TYPE_ALIASES = {
    "ambiguity": AMBIGUITY,
    "неоднозначность": AMBIGUITY,
    "contradiction": CONTRADICTION,
    "противоречие": CONTRADICTION,
}


def build_request(vacancy: Vacancy, known: list[Finding]) -> str:
    fields = {name: text[:MAX_FIELD_CHARS] for name, text in vacancy.fields.items()}
    payload = {
        "fields": fields,
        "known_findings": [{"topic": item.topic, "type": item.kind, "field": item.field} for item in known],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _locate(vacancy: Vacancy, field: str, quote: str) -> tuple[str, str] | None:
    """Находит цитату в названном поле, а если модель перепутала поле — в остальных."""
    field = clean_text(field)
    quote = clean_text(quote)
    if not quote:
        return None
    if field.startswith("address") and field not in vacancy.fields and vacancy.location_fields:
        field = vacancy.location_fields[0]
    candidates = [field] if field in vacancy.fields else []
    if len(quote) >= MIN_QUOTE_TO_SEARCH and not quote.isdigit():
        # Короткую цитату или голое число ищем только в названном поле: в чужом поле совпадёт что угодно.
        candidates += [name for name in vacancy.fields if name != field]
    for name in candidates:
        found = find_quote(vacancy.fields[name], quote)
        if found:
            if len(found) > MAX_QUOTE_CHARS:
                found = found[:MAX_QUOTE_CHARS].rsplit(" ", 1)[0]
            return name, found
    return None


def _sentence(value, limit: int = 400) -> str:
    text = clean_text(value)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


_QUOTED = re.compile(r"[«\"'“„]([^«»\"'“”„]{4,120})[»\"'”“]")


def _invents_quotes(text: str, vacancy: Vacancy) -> bool:
    """Модель ссылается в кавычках на фразу, которой в вакансии нет."""
    known = fold(" ".join(vacancy.fields.values()))
    for phrase in _QUOTED.findall(text):
        words = [word for word in re.findall(r"\w+", fold(phrase)) if len(word) > 3]
        if words and not all(word[: max(4, len(word) - 3)] in known for word in words):
            return True
    return False


def _invents_numbers(text: str, vacancy: Vacancy) -> bool:
    known = " ".join(vacancy.fields.values())
    return any(len(number) > 1 and number not in known for number in _NUMBER.findall(text))


def _same_point(rule: Finding, topic: str, kind: str, quote: str) -> bool:
    """Правило уже сообщило о том же: пропуск по теме или то же место текста."""
    if rule.topic != topic:
        return False
    if rule.kind == GAP:
        return True
    if rule.kind != kind:
        return False
    mine = _words(quote)
    for value in [rule.quote] + [side["value"] for side in rule.conflict]:
        theirs = _words(value)
        if mine and theirs and len(mine & theirs) * 2 >= min(len(mine), len(theirs)):
            return True  # цитаты пересекаются хотя бы наполовину: это одно и то же место
    return False


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"\w+", fold(text)) if len(word) > 2}


def validate(items, vacancy: Vacancy, known: list[Finding], facts: Facts, strict: bool = True) -> list[Finding]:
    """Отбирает из ответа модели замечания, которые подтверждаются текстом вакансии.

    ``strict`` требует признак расплывчатости в цитате неоднозначности (``TV_AI_STRICT``).
    """
    concrete = {
        "salary": facts.salary_concrete,
        "schedule": facts.schedule_concrete,
        "location": facts.location_concrete,
        "employment": facts.employment_concrete,
    }
    accepted: list[Finding] = []
    per_topic: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        topic = clean_text(item.get("topic")).lower()
        topic = _TOPIC_ALIASES.get(topic, topic)
        kind = _TYPE_ALIASES.get(clean_text(item.get("type")).lower())
        if topic not in TOPICS or kind is None:
            continue
        first = _locate(vacancy, item.get("field", ""), item.get("quote", ""))
        if first is None:
            continue  # цитаты в вакансии нет: замечание не на чем основать
        conflict: list[dict] = []
        if kind == CONTRADICTION:
            second = _locate(vacancy, item.get("second_field", ""), item.get("second_quote", ""))
            if second is None or second == first:
                continue
            conflict = [
                {"field": first[0], "value": first[1]},
                {"field": second[0], "value": second[1]},
            ]
        elif concrete.get(topic) and topic in GENERIC_VAGUE and GENERIC_VAGUE[topic].search(first[1]):
            continue  # по теме есть конкретные сведения: общие слова о ней не проблема
        elif strict and not _HEDGE.search(first[1]):
            continue  # в цитате нет ни оговорки, ни оценочного слова: это обычная конкретная фраза
        if any(_same_point(rule, topic, kind, first[1]) for rule in known):
            continue  # правила уже сказали об этом
        problem = _sentence(item.get("problem"))
        recommendation = _sentence(item.get("recommendation"))
        if len(problem) < MIN_PROBLEM_CHARS or not _CYRILLIC.search(problem) or _FOREIGN.search(problem + recommendation):
            continue  # пустое или оборванное объяснение: замечание редактору не поможет
        if _LEGAL.search(problem) or _invents_numbers(problem, vacancy) or _invents_quotes(problem, vacancy):
            continue
        if (
            not recommendation
            or not _CYRILLIC.search(recommendation)
            or _LEGAL.search(recommendation)
            or _invents_numbers(recommendation, vacancy)
            or _invents_quotes(recommendation, vacancy)
        ):
            recommendation = _FALLBACK_RECOMMENDATION[topic]
        key = (topic, first[1].lower())
        if key in seen or per_topic.get(topic, 0) >= MAX_PER_TOPIC:
            continue
        seen.add(key)
        per_topic[topic] = per_topic.get(topic, 0) + 1
        accepted.append(
            Finding(topic, kind, first[0], first[1], problem, recommendation, detected_by="ai", conflict=conflict)
        )
        if len(accepted) >= MAX_FINDINGS:
            break
    return accepted


def review(
    client: ChatClient, vacancy: Vacancy, known: list[Finding], facts: Facts, strict: bool = True
) -> tuple[list[Finding], str]:
    """Спрашивает модель. Возвращает только проверенные замечания и модель, которая ответила.

    Сбой всех моделей — ``AIError``.
    """
    reply, model = client.ask(SYSTEM_PROMPT, build_request(vacancy, known))
    return validate(reply.get("findings"), vacancy, known, facts, strict), model
