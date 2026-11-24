"""Учебный пример организаторов: что правила находят в каждой из десяти вакансий."""

import json
import unittest
from pathlib import Path

from transparent_vacancy import rules
from transparent_vacancy.vacancy import Vacancy, extract_vacancies

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "kantkod_sample_vacancies.json"

EXPECTED = {
    "DEMO-39-001": [],
    "DEMO-39-002": [
        ("duties", "ambiguity"),
        ("requirements", "gap"),
        ("salary", "gap"),
        ("schedule", "ambiguity"),
        ("location", "ambiguity"),
    ],
    "DEMO-39-003": [("requirements", "contradiction"), ("location", "ambiguity")],
    "DEMO-39-004": [("location", "contradiction")],
    "DEMO-39-005": [],
    "DEMO-39-006": [
        ("title", "ambiguity"),
        ("duties", "ambiguity"),
        ("requirements", "gap"),
        ("schedule", "ambiguity"),
        ("location", "ambiguity"),
    ],
    "DEMO-39-007": [],
    "DEMO-39-008": [("schedule", "ambiguity")],
    "DEMO-39-009": [],
    "DEMO-39-010": [("location", "ambiguity")],
}


class TrainingSampleTest(unittest.TestCase):
    def setUp(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        self.vacancies = [Vacancy(raw, index) for index, raw in enumerate(extract_vacancies(payload))]

    def test_every_vacancy_of_the_sample_is_read(self):
        self.assertEqual([vacancy.id for vacancy in self.vacancies], list(EXPECTED))

    def test_rules_find_the_planted_defects_and_nothing_in_clean_vacancies(self):
        for vacancy in self.vacancies:
            with self.subTest(id=vacancy.id):
                found = [(item.topic, item.kind) for item in rules.analyze(vacancy)[0]]
                self.assertEqual(found, EXPECTED[vacancy.id])

    def test_contradictions_carry_both_source_values(self):
        for vacancy in self.vacancies:
            for item in rules.analyze(vacancy)[0]:
                if item.kind == "contradiction":
                    with self.subTest(id=vacancy.id, topic=item.topic):
                        self.assertEqual(len(item.conflict), 2)
                        for side in item.conflict:
                            self.assertIn(side["value"], vacancy.get(side["field"]))
