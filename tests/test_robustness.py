"""Набор жюри может содержать что угодно: анализатор не должен падать и нарушать формат отчёта."""

import copy
import json
import random
import unittest

from tests.helpers import CLEAN, vacancy
from transparent_vacancy.analyzer import analyze_vacancies, build_report
from transparent_vacancy.config import Settings
from transparent_vacancy.html_report import render_html
from transparent_vacancy.letters import build_letters
from transparent_vacancy.vacancy import Vacancy, extract_vacancies

JUNK = [
    None, "", " ", [], {}, 0, 1, -5, 3.5, True, False, "0", "abc", ["x", None, 3], {"a": 1},
    {"text": None}, {"address": None}, {"address": "строка"}, {"address": True},
    {"address": [None, 5, {"location": None}, {"location": 7}]},
    "<b>жирный</b>", "от", "от 0", "9" * 40, [[1, 2]],
    {"education": 5, "experience": "три"}, {"experience": "2"}, {"experience": None},
]


def settings() -> Settings:
    result = Settings()
    result.ai_enabled = False
    return result


class RobustnessTest(unittest.TestCase):
    def test_broken_field_values_keep_the_report_format(self):
        randomizer = random.Random(39)
        keys = list(CLEAN)
        for _ in range(1500):
            raw = copy.deepcopy(CLEAN)
            for key in randomizer.sample(keys, randomizer.randint(1, 5)):
                if randomizer.random() < 0.25:
                    raw.pop(key)
                else:
                    raw[key] = copy.deepcopy(randomizer.choice(JUNK))
            entries, ai, duplicates = analyze_vacancies([raw], settings())
            report = build_report(entries, ai, "file", "fuzz", 1, duplicates)
            json.dumps(report)
            build_letters(report)
            render_html(report)
            self.assertEqual(len(entries), 1)
            for finding in entries[0]["findings"]:
                self.assertTrue(finding["field"] and finding["problem"] and finding["recommendation"], finding)
                self.assertEqual(finding["type"] == "gap", finding["quote"] == "", finding)
                self.assertEqual(finding["type"] == "contradiction", "conflict" in finding, finding)
                if "conflict" in finding:
                    self.assertEqual(len(finding["conflict"]), 2)
                    self.assertTrue(all(side["field"] and side["value"] for side in finding["conflict"]), finding)

    def test_address_that_is_not_a_list(self):
        for value in (True, 5, None, "г Калининград, Летняя улица, 3"):
            with self.subTest(value=value):
                Vacancy(vacancy(addresses={"address": value}))

    def test_input_of_the_wrong_shape_gives_no_vacancies(self):
        for payload in (None, 5, "строка", [], {}, [None, 1, "x", [], {}], {"results": None}, {"results": {"vacancies": None}}):
            with self.subTest(payload=payload):
                self.assertEqual(extract_vacancies(payload), [])

    def test_every_input_vacancy_gets_its_own_entry(self):
        raw = [vacancy(id="a"), {"id": "b"}, vacancy(id=None), vacancy(id=None), vacancy(id=7)]
        entries, _, _ = analyze_vacancies(raw, settings())
        self.assertEqual([entry["id"] for entry in entries], ["a", "b", None, None, "7"])


if __name__ == "__main__":
    unittest.main()
