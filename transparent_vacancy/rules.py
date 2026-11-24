"""Правила: пропуски и противоречия, которые видно без модели.

Главный принцип из задания: тема закрыта, если конкретные сведения есть хотя бы
в одном поле, структурированном или текстовом. Поэтому каждое правило сначала
ищет конкретику везде и только потом сообщает о проблеме.

Правила намеренно осторожны: сомнительный случай они пропускают. Расплывчатые
формулировки в свободном тексте дальше разбирает ИИ (``ai_review``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .findings import AMBIGUITY, CONTRADICTION, GAP, Finding
from .textutil import excerpt, fold
from .vacancy import Vacancy

I = re.IGNORECASE


@dataclass
class Facts:
    """Что правила узнали о вакансии: по этим признакам отсеиваются лишние замечания ИИ."""

    salary_concrete: bool = False
    schedule_concrete: bool = False
    location_concrete: bool = False
    employment_concrete: bool = False


def _search(vacancy: Vacancy, pattern: re.Pattern, skip: tuple[str, ...] = ()) -> tuple[str, str] | None:
    """Первое совпадение в свободном тексте: (поле, цитата)."""
    for name, text in vacancy.free_text():
        if name in skip:
            continue
        match = pattern.search(text)
        if match:
            return name, excerpt(text, match.start(), match.end())
    return None


def _money(value: float | None) -> str:
    return "" if value is None else str(int(value)) if float(value).is_integer() else str(value)


# --- название должности ------------------------------------------------------

_GENERIC_TITLES = {
    "специалист",
    "ведущий специалист",
    "главный специалист",
    "старший специалист",
    "младший специалист",
    "работник",
    "сотрудник",
    "менеджер",
    "рабочий",
    "помощник",
    "эксперт",
    "консультант",
    "инженер",
    "оператор",
    "мастер",
}


def check_title(vacancy: Vacancy) -> list[Finding]:
    title = vacancy.get("job-name")
    if not title:
        return [
            Finding(
                "title",
                GAP,
                "job-name",
                "",
                "Название должности не указано.",
                "Попросите работодателя указать название должности так, как оно записано в штатном расписании.",
            )
        ]
    if fold(title).strip(" .") in _GENERIC_TITLES:
        return [
            Finding(
                "title",
                AMBIGUITY,
                "job-name",
                title,
                "Название должности общее: по нему нельзя понять направление работы и подразделение.",
                "Уточните у работодателя полное название должности с направлением или участком работы.",
            )
        ]
    return []


# --- обязанности -------------------------------------------------------------

_OTHER_DUTIES = re.compile(
    r"(?:(?:и\s+)?(?:друг\w+|ин\w+|проч\w+)\s+(?:обязанност\w+|поручени\w+|функци\w+)"
    r"|(?:и\s+)?(?:друг\w+|ины[ехм]и?|проч\w+)\s+задач\w*"
    r"|выполнени\w+\s+(?:ин\w+|друг\w+|проч\w+|разов\w+|различн\w+|разн\w+)\s+поручени\w+"
    r"|(?:различн\w+|разн\w+)\s+поручени\w+)"
    r"(?:\s+руководител\w+)?(?:\s+по\s+(?:мере\s+)?необходимости)?",
    I,
)
_MIN_DUTY_CHARS = 25  # короче этого описание обязанностей ни о чём не говорит
_BY_INSTRUCTION = re.compile(
    r"(?:согласно|в\s+соответствии\s+с|по)\s+должностн\w+\s+(?:инструкци\w+|обязанност\w+)", I
)


def check_duties(vacancy: Vacancy) -> list[Finding]:
    duty = vacancy.get("duty")
    if not duty:
        if vacancy.get("skills"):
            return []
        return [
            Finding(
                "duties",
                GAP,
                "duty",
                "",
                "Обязанности не описаны: поле пустое, перечня навыков тоже нет.",
                "Попросите работодателя перечислить основные обязанности: что именно сотрудник делает в течение рабочего дня.",
            )
        ]
    if fold(duty) == fold(vacancy.get("job-name")) or len(duty) < _MIN_DUTY_CHARS:
        return [
            Finding(
                "duties",
                AMBIGUITY,
                "duty",
                duty,
                "Описание обязанностей слишком короткое: из него не видно, какие задачи входят в работу.",
                "Попросите работодателя перечислить конкретные задачи и участок работы.",
            )
        ]
    match = _BY_INSTRUCTION.search(duty)
    if match and len(duty) < 120:
        return [
            Finding(
                "duties",
                AMBIGUITY,
                "duty",
                excerpt(duty, match.start(), match.end()),
                "Обязанности описаны ссылкой на должностную инструкцию, которой в вакансии нет.",
                "Попросите работодателя привести в вакансии основные обязанности из должностной инструкции.",
            )
        ]
    match = _OTHER_DUTIES.search(duty)
    if match and len((duty[: match.start()] + duty[match.end() :]).strip(" .,;:")) < _MIN_DUTY_CHARS:
        return [
            Finding(
                "duties",
                AMBIGUITY,
                "duty",
                duty,
                "Обязанности описаны общими словами: не названа ни одна конкретная задача.",
                "Попросите работодателя перечислить конкретные задачи и участок работы.",
            )
        ]
    if match:
        return [
            Finding(
                "duties",
                AMBIGUITY,
                "duty",
                excerpt(duty, match.start(), match.end()),
                "Перечень обязанностей открытый: неясно, какие ещё задачи может получить сотрудник.",
                "Уточните у работодателя, какие дополнительные поручения имеются в виду и как часто они бывают.",
            )
        ]
    return []


# --- требования --------------------------------------------------------------

_EDUCATION_WORDS = re.compile(
    r"образовани\w+|диплом\w*|\bвысшее\b|средне\w*[-\s]+(?:специальн|профессиональн)\w+|удостоверени\w+|аккредитац\w+",
    I,
)
_EXPERIENCE_WORDS = re.compile(r"\bопыт\w*|\bстаж\w*", I)
_EXPERIENCE_YEARS = re.compile(
    r"(?:опыт\w*|стаж\w*)[^.;|]{0,60}?(?:\bот|не\s+менее|более|свыше)\s+(\d{1,2})\s*(?:[-–]?\s*х\s*)?(?:лет|года|год)\b"
    # и обратный порядок: «не менее 5 лет опыта работы»
    r"|(?:\bот|не\s+менее|более|свыше)\s+(\d{1,2})\s*(?:[-–]?\s*х\s*)?(?:лет|года|год)\s+(?:\w+\s+){0,2}?(?:опыт\w+|стаж\w+)",
    I,
)
_EXPERIENCE_NONE = re.compile(
    r"без\s+опыта|опыт\w*(?:\s+работы)?\s*:?\s*(?:не\s+требуется|не\s+обязателен|необязателен)", I
)
_EXPERIENCE_WAIVER = re.compile(r"без\s+(?:предъявления\s+)?требовани\w+\s+к\s+(?:стажу|опыту)", I)
_OPTIONAL = re.compile(r"желател\w+|приветству\w+|преимуществ\w+|будет\s+плюсом|предпочтител\w+", I)
_HIGHER_EDUCATION = re.compile(
    r"высш\w+\s+(?:\w+\s+){0,2}образовани\w+|образовани\w+\s*[:—–-]?\s*высш\w+", I
)
# Признаки того, что в тексте требований названо хоть что-то проверяемое: знания, навыки, документы.
_CONCRETE_REQUIREMENT = re.compile(
    r"знани\w+|\bзнать\b|навык\w*|умени\w+|\bуметь\b|владени\w+|\bвладеть\b|квалификац\w+|специальност\w+|професси\w+"
    r"|удостоверени\w+|сертификат\w*|свидетельств\w+|допуск\w*|категори\w+|разряд\w*|\bправа\b|книжк\w+|справк\w+"
    r"|обучени\w+|курс\w+|компьютер\w*|\bпк\b|программ\w+|язык\w*|\bречь\w*|грамотн\w+|интернет\w*",
    I,
)
_NO_EDUCATION_NEEDED = ("ребования не предъявляются", "общее образование")
_REQUIREMENT_TEXTS = ("requirements", "qualification", "requirement.qualification")


def _optional_nearby(text: str, start: int, end: int) -> bool:
    return bool(_OPTIONAL.search(text[max(0, start - 50) : end + 30]))


def _names_concrete_requirement(texts: list[tuple[str, str]]) -> bool:
    """В тексте требований есть проверяемое условие, а не только личные качества и пожелания."""
    for _, text in texts:
        if _CONCRETE_REQUIREMENT.search(text):
            return True
        for match in _EXPERIENCE_WORDS.finditer(text):
            if not _optional_nearby(text, match.start(), match.end()):
                return True
    return False


def check_requirements(vacancy: Vacancy) -> list[Finding]:
    findings: list[Finding] = []
    texts = [(name, vacancy.get(name)) for name in _REQUIREMENT_TEXTS if vacancy.get(name)]
    everything = " ".join(text for _, text in vacancy.free_text())
    education = vacancy.get("requirement.education")
    experience = vacancy.number("requirement.experience")

    if not texts and not education and experience is None:
        return [
            Finding(
                "requirements",
                GAP,
                "requirements",
                "",
                "Требования к кандидату не указаны: нет ни текста требований, ни сведений об образовании и опыте.",
                "Попросите работодателя указать требуемое образование, опыт работы и обязательные навыки или документы.",
            )
        ]

    # Незаполненное образование само по себе не замечание: если требования названы по существу
    # (знания, навыки, документы), соискателю понятно, чего от него ждут.
    if not education and not _EDUCATION_WORDS.search(everything) and not _names_concrete_requirement(texts):
        if texts:
            problem = (
                "Требования названы общими словами: поле образования не заполнено, а в тексте нет ни уровня "
                "образования, ни конкретных знаний и навыков."
            )
            advice = (
                "Уточните у работодателя требуемый уровень образования и обязательные знания или навыки "
                "либо отметьте, что требований к образованию нет."
            )
        else:
            problem = "Не указано, какое образование требуется: поле образования не заполнено, текста требований нет."
            advice = "Уточните у работодателя требуемый уровень образования или отметьте, что требований к образованию нет."
        findings.append(Finding("requirements", GAP, "requirement.education", "", problem, advice))
    if experience is None and not _EXPERIENCE_WORDS.search(everything):
        findings.append(
            Finding(
                "requirements",
                GAP,
                "requirement.experience",
                "",
                "Не указано, нужен ли опыт работы: поле опыта пустое, в описании об этом тоже не сказано.",
                "Уточните у работодателя требуемый опыт работы в годах или отметьте, что опыт не требуется.",
            )
        )

    if experience is not None:
        findings.extend(_experience_conflict(vacancy, experience, everything))
    if education:
        findings.extend(_education_conflict(vacancy, education))
    return findings


def _experience_conflict(vacancy: Vacancy, experience: float, everything: str) -> list[Finding]:
    stated = vacancy.get("requirement.experience")
    if _EXPERIENCE_WAIVER.search(everything):
        return []  # в тексте перечислены варианты «без стажа или со стажем»: сравнивать не с чем
    mentions: list[tuple[str, str, int]] = []
    for name, text in vacancy.free_text():
        for match in _EXPERIENCE_YEARS.finditer(text):
            if _optional_nearby(text, match.start(), match.end()):
                continue
            mentions.append((name, excerpt(text, match.start(), match.end()), int(match.group(1) or match.group(2))))
    years = {value for _, _, value in mentions}
    if len(years) == 1 and years != {int(experience)}:
        name, quote, value = mentions[0]
        return [
            Finding(
                "requirements",
                CONTRADICTION,
                "requirement.experience",
                stated,
                f"Требуемый опыт указан по-разному: в поле опыта {stated} (лет), в тексте — от {value}.",
                "Уточните у работодателя, какой опыт работы действительно обязателен, и приведите оба места к одному значению.",
                conflict=[
                    {"field": "requirement.experience", "value": stated},
                    {"field": name, "value": quote},
                ],
            )
        ]
    if not mentions and experience >= 1:
        found = _search(vacancy, _EXPERIENCE_NONE)
        if found:
            name, quote = found
            return [
                Finding(
                    "requirements",
                    CONTRADICTION,
                    "requirement.experience",
                    stated,
                    f"В поле опыта указано {stated} (лет), а в тексте сказано, что опыт не требуется.",
                    "Уточните у работодателя, обязателен ли опыт работы, и приведите оба места к одному значению.",
                    conflict=[
                        {"field": "requirement.experience", "value": stated},
                        {"field": name, "value": quote},
                    ],
                )
            ]
    return []


def _education_conflict(vacancy: Vacancy, education: str) -> list[Finding]:
    if not any(marker in fold(education) for marker in _NO_EDUCATION_NEEDED):
        return []
    if fold(education).startswith("среднее общее"):
        return []
    for name in _REQUIREMENT_TEXTS:
        text = vacancy.get(name)
        match = _HIGHER_EDUCATION.search(text) if text else None
        if not match or _optional_nearby(text, match.start(), match.end()):
            continue
        quote = excerpt(text, match.start(), match.end())
        return [
            Finding(
                "requirements",
                CONTRADICTION,
                "requirement.education",
                education,
                "Уровень образования указан по-разному: в поле образования высшее не требуется, в тексте требований оно названо.",
                "Уточните у работодателя, какое образование обязательно, и приведите поле и текст к одному значению.",
                conflict=[
                    {"field": "requirement.education", "value": education},
                    {"field": name, "value": quote},
                ],
            )
        ]
    return []


# --- оплата ------------------------------------------------------------------

_SALARY_WORD = re.compile(r"заработн\w+\s+плат\w+|зарплат\w+|\bз\s*/\s*п\b|\bзп\b|оплат\w+\s+труда|\bдоход\w*", I)
_AMOUNT = re.compile(
    r"(?:\b(от|до)\s+)?(?<!\d)(\d{2,3})[  ]?(\d{3})(?!\d)|(?:\b(от|до)\s+)?(?<!\d)(\d{2,3})\s*(?:тыс|т\.\s*р)", I
)
_NOT_SALARY = re.compile(r"оклад|преми|надбавк|доплат|компенсац|аренд|найм|жиль|подъ[её]мн|выплат\w+\s+в\s+размере", I)
_VAGUE_SALARY = re.compile(
    r"по\s+(?:результатам|итогам)\s+собеседовани\w+|по\s+договор[её]нности|договорн\w+"
    r"|(?:достойн\w+|высок\w+|конкурентн\w+|стабильн\w+)\s+(?:заработн\w+\s+плат\w+|зарплат\w+|оплат\w+|доход\w*)",
    I,
)
_PAY_FACTORS = re.compile(r"преми\w+|надбавк\w+|стимулир\w+|сдельн\w+|процент\w*|kpi|выработк\w+|категори\w+|разряд\w*|стаж\w*|нагрузк\w+|ставк\w+", I)


def _text_amounts(vacancy: Vacancy) -> list[tuple[str, str, str, float]]:
    """Суммы зарплаты, названные в свободном тексте: (поле, цитата, «от»/«до»/«», сумма)."""
    result: list[tuple[str, str, str, float]] = []
    for name, text in vacancy.free_text():
        for word in _SALARY_WORD.finditer(text):
            tail_end = min(len(text), word.end() + 90)
            stop = re.search(r"[;|]|\.\s", text[word.end() : tail_end])
            if stop:
                tail_end = word.end() + stop.start()
            for amount in _AMOUNT.finditer(text, word.end(), tail_end):
                if _NOT_SALARY.search(text[word.end() : amount.start()]):
                    break
                if amount.group(2):
                    bound, value = amount.group(1) or "", float(amount.group(2) + amount.group(3))
                else:
                    bound, value = amount.group(4) or "", float(amount.group(5)) * 1000
                if value >= 10000:
                    result.append((name, excerpt(text, word.start(), amount.end()), bound.lower(), value))
    return result


def check_salary(vacancy: Vacancy, facts: Facts) -> list[Finding]:
    low = vacancy.number("salary_min") or 0
    high = vacancy.number("salary_max") or 0
    label = vacancy.get("salary")
    label_number = re.search(r"\d[\d  ]*", label)
    amounts = _text_amounts(vacancy)
    structured = low > 0 or high > 0 or bool(label_number)
    facts.salary_concrete = structured or bool(amounts)

    if not facts.salary_concrete:
        vague = _search(vacancy, _VAGUE_SALARY)
        if vague or label:
            name, quote = vague if vague else ("salary", label)
            return [
                Finding(
                    "salary",
                    AMBIGUITY,
                    name,
                    quote,
                    "Размер оплаты описан словами без суммы: соискатель не может оценить доход.",
                    "Уточните у работодателя размер оплаты: сумму или вилку в рублях и из чего она складывается.",
                )
            ]
        return [
            Finding(
                "salary",
                GAP,
                "salary",
                "",
                "Размер оплаты не указан ни в полях зарплаты, ни в описании.",
                "Попросите работодателя указать размер оплаты: сумму или вилку в рублях.",
            )
        ]

    findings: list[Finding] = []
    if low > 0 and high > 0 and low > high:
        findings.append(
            Finding(
                "salary",
                CONTRADICTION,
                "salary_min",
                _money(low),
                f"Минимальная зарплата ({_money(low)}) больше максимальной ({_money(high)}).",
                "Уточните у работодателя нижнюю и верхнюю границы оплаты и исправьте поля.",
                conflict=[
                    {"field": "salary_min", "value": _money(low)},
                    {"field": "salary_max", "value": _money(high)},
                ],
            )
        )
    elif label_number and low > 0:
        shown = float(re.sub(r"\D", "", label_number.group(0)) or 0)
        if shown >= 1000 and shown not in (low, high):
            findings.append(
                Finding(
                    "salary",
                    CONTRADICTION,
                    "salary",
                    label,
                    f"Сумма в строке зарплаты («{label}») не совпадает с границами в числовых полях ({_money(low)}–{_money(high)}).",
                    "Уточните у работодателя актуальный размер оплаты и приведите поля к одному значению.",
                    conflict=[
                        {"field": "salary", "value": label},
                        {"field": "salary_min", "value": _money(low)},
                    ],
                )
            )

    bounds = [value for value in (low, high) if value > 0]
    if bounds and not findings:
        floor, ceiling = min(bounds), max(bounds)
        for name, quote, bound, value in amounts:
            below = value < floor * 0.9
            above = value > ceiling * 1.1
            clash = below if bound == "до" else (below or above)
            if bound == "" and len(bounds) == 1:
                clash = False  # одна граница в полях и сумма без «от/до» в тексте: сравнивать нечего
            if clash:
                where = f"{_money(floor)}–{_money(ceiling)}" if floor != ceiling else f"от {_money(floor)}"
                field_name = "salary_min" if low > 0 else "salary_max"
                findings.append(
                    Finding(
                        "salary",
                        CONTRADICTION,
                        field_name,
                        _money(low if low > 0 else high),
                        f"Размер оплаты указан по-разному: в полях зарплаты {where}, в описании другая сумма.",
                        "Уточните у работодателя, какая сумма актуальна и за что она начисляется, и приведите поля и описание к одному значению.",
                        conflict=[
                            {"field": "salary_min, salary_max", "value": where},
                            {"field": name, "value": quote},
                        ],
                    )
                )
                break

    if not findings and low > 0 and high >= low * 2.5:
        everything = " ".join(text for _, text in vacancy.free_text())
        if not _PAY_FACTORS.search(everything):
            findings.append(
                Finding(
                    "salary",
                    AMBIGUITY,
                    "salary_max",
                    f"salary_min: {_money(low)}, salary_max: {_money(high)}",
                    "Вилка оплаты широкая, и нигде не сказано, от чего зависит сумма.",
                    "Уточните у работодателя, от чего зависит размер оплаты внутри вилки: нагрузка, квалификация, премии.",
                )
            )
    return findings


# --- график ------------------------------------------------------------------

_ROTATION = r"(?<![\d/,.])\d\s*/\s*\d(?![\d/]|\s*ставк)"
_SCHEDULE_DETAILS = re.compile(
    r"\b[сc]\s*\d{1,2}(?:\s*[:.\-]\s*\d{2})?\s*(?:ч\.?|час\w*)?\s*(?:до|по)\s*\d{1,2}(?:\s*[:.\-]\s*\d{2})?"
    r"|\b\d{1,2}[:.]\d{2}\s*(?:[-–—]|до)\s*\d{1,2}[:.]\d{2}\b"
    rf"|{_ROTATION}"
    r"|сутки\s*(?:через|/)\s*\w+"
    r"|\b(?:день|два|две|три|четыре|\d)\s+(?:дня\s+|суток\s+)?через\s+(?:день|два|две|три|четыре|\d)\b"
    r"|\b(?:пяти|шести)дневн\w*|\b[56]\s*-?\s*(?:ти\s*)?дневн\w*"
    r"|\bпн\.?\s*[-–—]\s*(?:пт|сб)\b|понедельник\w*\s*(?:по|[-–—])\s*(?:пятниц|суббот)\w*"
    r"|\b\d{1,2}(?:[,.]\d)?\s*[-–—]?\s*(?:ти\s*)?час\w*\s+(?:рабоч\w+\s+)?(?:день|дня|смен\w*|недел\w*)"
    r"|\b\d{1,2}\s*час\w*\s+в\s+(?:день|неделю|смену|сутки)"
    r"|рабочие\s+часы\s*:?\s*\d{1,2}"
    r"|\b[01][,.]\d{1,2}\s*ставк\w*|\bпол\s*ставки\b",
    I,
)
_SHIFT_WORK = re.compile(
    r"сменн\w+\s+(?:график\w*|режим\w*|работ\w+)|(?:график|режим)\w*\s+(?:работы\s*)?[:—–-]?\s*сменн\w+"
    r"|посменн\w+|сутки\s*(?:через|/)\s*\w+|(?<![\d/,.])[1-4]\s*/\s*[1-4](?![\d/]|\s*ставк)|вахтов\w+\s+метод\w*",
    I,
)
_VAGUE_SCHEDULE = re.compile(
    r"(?:гибк\w+|свободн\w+|удобн\w+|плавающ\w+|скользящ\w+|индивидуальн\w+)\s+график\w*"
    r"|график\w*\s+(?:работы\s+)?(?:по\s+(?:договор[её]нности|согласованию)|обсуждается|гибк\w+|свободн\w+|плавающ\w+)"
    r"|ненормированн\w+\s+рабоч\w+\s+(?:день|время)|гибк\w+\s+(?:рабоч\w+\s+)?врем\w+",
    I,
)
_FLEXIBLE = r"(?:гибк\w+|свободн\w+|плавающ\w+|скользящ\w+|индивидуальн\w+)\s+график\w*"
_OPTIONAL_FLEXIBLE = re.compile(
    rf"(?:возмож\w+|допуска\w+)\s+(?:\w+\s+){{0,2}}?{_FLEXIBLE}"
    rf"|{_FLEXIBLE}\s+(?:\w+\s+){{0,2}}?(?:возмож\w+|по\s+согласованию|по\s+договор[её]нности|обсуждается)",
    I,
)
_PART_RATE = re.compile(r"\b0[,.]\d{1,2}\s*ставк\w*|\bпол\s*ставки\b", I)


def check_schedule(vacancy: Vacancy, facts: Facts) -> list[Finding]:
    schedule = vacancy.get("schedule")
    shift = vacancy.get("shift")
    details = _search(vacancy, _SCHEDULE_DETAILS)
    if not details:
        # Расписание бывает вписано прямо в поле графика: «Сменный график: 2/2, 08:00–20:00».
        for name in ("schedule", "shift"):
            match = _SCHEDULE_DETAILS.search(vacancy.get(name))
            if match:
                details = (name, excerpt(vacancy.get(name), match.start(), match.end()))
                break
    facts.schedule_concrete = bool(details)
    kind = fold(schedule)

    if not schedule and not shift and not details:
        vague = _search(vacancy, _VAGUE_SCHEDULE)
        if vague:
            return [_vague_schedule(*vague)]
        return [
            Finding(
                "schedule",
                GAP,
                "schedule",
                "",
                "График работы не указан ни в поле графика, ни в описании.",
                "Попросите работодателя указать режим работы: рабочие дни, время начала и окончания, выходные.",
            )
        ]

    findings: list[Finding] = []
    if "полный рабочий день" in kind and "неполн" not in kind and not shift:
        clash = _search(vacancy, _SHIFT_WORK)
        if clash:
            name, quote = clash
            findings.append(
                Finding(
                    "schedule",
                    CONTRADICTION,
                    "schedule",
                    schedule,
                    "График указан по-разному: в поле графика полный рабочий день, в описании — сменный режим.",
                    "Уточните у работодателя фактический режим работы и приведите поле графика и описание к одному значению.",
                    conflict=[{"field": "schedule", "value": schedule}, {"field": name, "value": quote}],
                )
            )
    if details and not findings:
        optional = _search(vacancy, _OPTIONAL_FLEXIBLE)
        if optional:
            name, quote = optional
            findings.append(
                Finding(
                    "schedule",
                    AMBIGUITY,
                    name,
                    quote,
                    "Расписание названо, но рядом сказано, что возможен гибкий график: неясно, кому и на каких условиях он доступен.",
                    "Уточните у работодателя, обязательно ли названное расписание и по каким правилам согласуется гибкий график.",
                )
            )
    if findings or details:
        return findings

    if "сменн" in kind or "вахт" in kind or (shift and not schedule):
        value = ", ".join(part for part in (schedule, shift) if part)
        findings.append(
            Finding(
                "schedule",
                AMBIGUITY,
                "schedule" if schedule else "shift",
                value,
                "Указан сменный режим, но конкретного расписания нет ни в полях, ни в описании: неизвестны длительность смен и порядок чередования.",
                "Уточните у работодателя длительность смены, время начала и окончания и порядок чередования рабочих и выходных дней.",
            )
        )
    elif "неполн" in kind:
        findings.append(
            Finding(
                "schedule",
                AMBIGUITY,
                "schedule",
                schedule,
                "Указана неполная занятость по времени, но не сказано, сколько часов в день или дней в неделю нужно работать.",
                "Уточните у работодателя продолжительность рабочего дня и число рабочих дней в неделю или долю ставки.",
            )
        )
    elif _VAGUE_SCHEDULE.search(schedule) or "гибк" in kind or "ненормир" in kind or "свободн" in kind:
        findings.append(_vague_schedule("schedule", schedule))
    else:
        vague = _search(vacancy, _VAGUE_SCHEDULE)
        if vague:
            findings.append(_vague_schedule(*vague))
        elif schedule:
            facts.schedule_concrete = True  # в поле названо определённое расписание, например полный рабочий день
    return findings


def _vague_schedule(name: str, quote: str) -> Finding:
    return Finding(
        "schedule",
        AMBIGUITY,
        name,
        quote,
        "Формулировка о графике не называет рабочие дни и часы, а конкретного расписания нет ни в одном поле.",
        "Уточните у работодателя рабочие дни, время начала и окончания работы и то, кто и как согласует график.",
    )


# --- место работы ------------------------------------------------------------

_FULLY_REMOTE = re.compile(
    r"(?:полностью|исключительно|только)\s+(?:дистанционн|удал[её]нн)\w+"
    r"|(?:дистанционн|удал[её]нн)\w+\s+(?:\w+\s+){0,2}?из\s+любо\w+\s+(?:города|региона|места|точки)",
    I,
)
_ON_SITE_REQUIRED = re.compile(
    r"(?:обязательн\w+|ежедневн\w+|постоянн\w+)\s+(?:\w+\s+){0,2}?присутстви\w+\s+(?:в|на)\s+(?:офисе|рабочем\s+месте|территории)"
    r"|присутстви\w+\s+в\s+офисе\s+обязательн\w+|работа\s+(?:только|исключительно)\s+в\s+офисе",
    I,
)
_STREET = re.compile(
    r"\bул\b|улиц\w*|\bпр-?к?т\b|проспект\w*|\bпер\b|переул\w+|шоссе|\bнаб\b|набережн\w+|бульвар\w*|\bб-р\b"
    r"|площад\w+|\bпл\b|проезд\w*|\bмкр\b|микрорайон\w*|тупик\w*|алле\w+|квартал\w*",
    I,
)
_REMOTE = re.compile(r"удал[её]нн\w+\s+(?:работ\w+|формат\w*|режим\w*)|дистанционн\w+\s+(?:работ\w+|формат\w*|режим\w*)|работа\s+на\s+дому", I)


def _format_conflict(vacancy: Vacancy) -> list[Finding]:
    """В одном месте работа полностью дистанционная, в другом обязательно присутствие в офисе."""
    remote = _search(vacancy, _FULLY_REMOTE)
    on_site = _search(vacancy, _ON_SITE_REQUIRED)
    if not remote or not on_site:
        return []
    return [
        Finding(
            "location",
            CONTRADICTION,
            remote[0],
            remote[1],
            "Место работы указано по-разному: в одном месте работа полностью дистанционная, в другом обязательно присутствие в офисе.",
            "Уточните у работодателя, где выполняется работа: дистанционно или в офисе, и приведите описание к одному варианту.",
            conflict=[
                {"field": remote[0], "value": remote[1]},
                {"field": on_site[0], "value": on_site[1]},
            ],
        )
    ]


def check_location(vacancy: Vacancy, facts: Facts) -> list[Finding]:
    return _format_conflict(vacancy) + _check_address(vacancy, facts)


def _check_address(vacancy: Vacancy, facts: Facts) -> list[Finding]:
    names = vacancy.location_fields
    if not names:
        if _search(vacancy, _REMOTE):
            facts.location_concrete = True
            return []
        return [
            Finding(
                "location",
                GAP,
                "addresses.address",
                "",
                "Место работы не указано: адреса нет ни в поле адреса, ни в описании.",
                "Попросите работодателя указать адрес места работы: населённый пункт, улицу и дом.",
            )
        ]
    exact = [name for name in names if re.search(r"\d", vacancy.get(name))]
    facts.location_concrete = bool(exact)
    if exact:
        return []
    name = names[0]
    if _STREET.search(vacancy.get(name)):
        problem = "Адрес места работы неполный: нет номера дома, по такому адресу нельзя оценить дорогу."
    else:
        problem = "Адрес места работы неполный: назван только населённый пункт или регион, без улицы и дома."
    return [
        Finding(
            "location",
            AMBIGUITY,
            name,
            vacancy.get(name),
            problem,
            "Уточните у работодателя точный адрес места работы: улицу и номер дома.",
        )
    ]


# --- тип занятости -----------------------------------------------------------

_EMPLOYMENT_WORDS = re.compile(
    r"(?:полн\w+|частичн\w+|неполн\w+|постоянн\w+|временн\w+)\s+занятост\w+"
    r"|временн\w+\s+(?:работ\w+|трудоустройств\w+)|сезонн\w+\s+работ\w+|совместител\w+|стажировк\w+|подработк\w+"
    r"|постоянн\w+\s+(?:работ\w+|основ\w+)|срочн\w+\s+(?:трудов\w+\s+)?договор\w*"
    r"|на\s+время\s+(?:декрет\w+|отпуска|отсутствия)|\b[01][,.]\d{1,2}\s*ставк\w*|\bпол\s*ставки\b",
    I,
)
_TERM = re.compile(
    r"на\s+(?:срок\s+)?\d+\s*(?:мес|недел|дн|год|лет)|\bдо\s+\d{1,2}[.\s]\S+|на\s+(?:время|период)\s+\w+|сезон\w*\s+\w+",
    I,
)


def check_employment(vacancy: Vacancy, facts: Facts) -> list[Finding]:
    employment = vacancy.get("employment")
    schedule = vacancy.get("schedule")
    facts.employment_concrete = bool(employment)
    if not employment:
        if _search(vacancy, _EMPLOYMENT_WORDS):
            facts.employment_concrete = True
            return []
        return [
            Finding(
                "employment",
                GAP,
                "employment",
                "",
                "Тип занятости не указан: поле пустое, в описании тоже не сказано, полная это занятость, частичная или временная.",
                "Уточните у работодателя тип занятости: полная, частичная, временная или сезонная работа.",
            )
        ]

    kind, hours = fold(employment), fold(schedule)
    part_time_hours = "неполн" in hours
    full_time_hours = "полный рабочий день" in hours and not part_time_hours
    if ("полная" in kind and part_time_hours) or ("частичн" in kind and full_time_hours):
        return [
            Finding(
                "employment",
                CONTRADICTION,
                "employment",
                employment,
                "Тип занятости и график не согласуются: одно поле говорит о полной занятости, другое о неполной.",
                "Уточните у работодателя, полная это занятость или частичная, и приведите оба поля к одному значению.",
                conflict=[
                    {"field": "employment", "value": employment},
                    {"field": "schedule", "value": schedule},
                ],
            )
        ]
    if "полная" in kind:
        rate = _search(vacancy, _PART_RATE)
        if rate:
            name, quote = rate
            return [
                Finding(
                    "employment",
                    CONTRADICTION,
                    "employment",
                    employment,
                    "В поле занятости указана полная занятость, а в описании — работа на долю ставки.",
                    "Уточните у работодателя, на какую долю ставки открыта вакансия, и исправьте тип занятости.",
                    conflict=[
                        {"field": "employment", "value": employment},
                        {"field": name, "value": quote},
                    ],
                )
            ]
    if ("временн" in kind or "сезонн" in kind) and not _search(vacancy, _TERM):
        return [
            Finding(
                "employment",
                AMBIGUITY,
                "employment",
                employment,
                "Работа временная, но срок нигде не назван.",
                "Уточните у работодателя, на какой срок открыта вакансия и возможен ли переход на постоянную работу.",
            )
        ]
    return []


# Общие слова о теме. Если по теме есть конкретные сведения, такая фраза не проблема.
GENERIC_VAGUE = {"salary": _VAGUE_SALARY, "schedule": _VAGUE_SCHEDULE}


def analyze(vacancy: Vacancy) -> tuple[list[Finding], Facts]:
    """Все замечания правил по вакансии и признаки для фильтра ответов ИИ."""
    facts = Facts()
    findings: list[Finding] = []
    findings += check_title(vacancy)
    findings += check_duties(vacancy)
    findings += check_requirements(vacancy)
    findings += check_salary(vacancy, facts)
    findings += check_schedule(vacancy, facts)
    findings += check_location(vacancy, facts)
    findings += check_employment(vacancy, facts)
    return findings, facts
