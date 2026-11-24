"""Замечание отчёта и справочник тем."""

from __future__ import annotations

from dataclasses import dataclass, field

# Темы из задания: что соискатель должен понять из вакансии.
TOPICS = {
    "title": "Название должности",
    "duties": "Обязанности",
    "requirements": "Требования",
    "salary": "Оплата",
    "schedule": "График",
    "location": "Место работы",
    "employment": "Тип занятости",
}

GAP = "gap"  # сведений нет нигде
AMBIGUITY = "ambiguity"  # формулировка есть, но конкретики из неё не извлечь
CONTRADICTION = "contradiction"  # два значения не согласуются
NOTE = "note"  # пометка: поле не заполнено, но сведения есть в другом поле; на понимание условий не влияет

KINDS = {
    GAP: "Пропуск",
    AMBIGUITY: "Неоднозначность",
    CONTRADICTION: "Противоречие",
    NOTE: "Пометка",
}


@dataclass
class Finding:
    topic: str
    kind: str
    field: str
    quote: str
    problem: str
    recommendation: str
    detected_by: str = "rule"  # rule | ai
    conflict: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        data = {
            "topic": self.topic,
            "topic_title": TOPICS.get(self.topic, self.topic),
            "type": self.kind,
            "type_title": KINDS.get(self.kind, self.kind),
            "field": self.field,
            "quote": self.quote,
            "problem": self.problem,
            "recommendation": self.recommendation,
            "detected_by": self.detected_by,
        }
        if self.kind == CONTRADICTION:
            data["conflict"] = self.conflict
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Finding":
        return cls(
            topic=data["topic"],
            kind=data["type"],
            field=data.get("field", ""),
            quote=data.get("quote", ""),
            problem=data.get("problem", ""),
            recommendation=data.get("recommendation", ""),
            detected_by=data.get("detected_by", "rule"),
            conflict=list(data.get("conflict") or []),
        )
