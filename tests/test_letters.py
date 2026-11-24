import json
import unittest

from tests.helpers import vacancy
from tests.test_cli import CliCase
from transparent_vacancy.analyzer import analyze_vacancies
from transparent_vacancy.config import Settings
from transparent_vacancy.letters import build_letter, build_letters


def entries(*raw):
    settings = Settings()
    settings.ai_enabled = False
    return analyze_vacancies(list(raw), settings)[0]


class LetterTest(unittest.TestCase):
    def test_no_findings_no_letter(self):
        (entry,) = entries(vacancy())
        self.assertIsNone(build_letter(entry))

    def test_gap_quote_and_contradiction_are_worded_from_the_report(self):
        (entry,) = entries(
            vacancy(
                employment="Полная занятость",
                schedule="Неполный рабочий день/неполная рабочая неделя",
                salary="", salary_min=0, salary_max=0,
            )
        )
        text = build_letter(entry)
        self.assertIn("«Медицинская сестра процедурной»", text)
        self.assertIn("Кому: ГБУЗ «Городская больница № 1»", text)
        self.assertIn("этих сведений нет (поле salary). Укажите, пожалуйста, размер оплаты", text)
        self.assertIn("указано: «Неполный рабочий день/неполная рабочая неделя» (поле schedule)", text)
        self.assertIn("два разных значения: «Полная занятость» (поле employment)", text)
        self.assertNotIn("у работодателя", text)  # письмо обращено к самому работодателю
        self.assertEqual(text.count("\n1. "), 1)

    def test_letter_adds_no_numbers_of_its_own(self):
        import re

        raw = vacancy(schedule="Сменная работа", employment=None)
        (entry,) = entries(raw)
        source = json.dumps(raw, ensure_ascii=False)
        body = build_letter(entry).split("просим уточнить:")[1]
        for number in re.findall(r"\d{2,}", body):
            self.assertIn(number, source)

    def test_advice_in_an_unknown_form_is_kept_as_written(self):
        (entry,) = entries(vacancy(employment=None))
        entry["findings"][0]["recommendation"] = "Проверьте, верно ли указан тип занятости."
        self.assertIn("Проверьте, верно ли указан тип занятости.", build_letter(entry))

    def test_file_names(self):
        letters = build_letters({"vacancies": entries(vacancy(id="a/b:1", employment=None), vacancy(id=None, employment=None), vacancy(id="ok"))})
        self.assertEqual(sorted(letters), ["a_b_1.md", "vacancy-2.md"])


class LettersCommandTest(CliCase):
    def test_letters_from_report(self):
        self.portal.vacancies[0]["employment"] = "Частичная занятость"
        self.call("run", "--no-ai")
        code, output = self.call("letters")
        self.assertEqual(code, 0)
        files = sorted(path.name for path in (self.dir / "reports" / "letters").iterdir())
        self.assertEqual(files, ["id-0.md"])
        self.assertIn("писем: 1 из 6", output)

    def test_one_vacancy_and_unknown_id(self):
        self.portal.vacancies[0]["employment"] = "Частичная занятость"
        self.call("run", "--no-ai")
        self.assertEqual(self.call("letters", "--id", "id-0")[0], 0)
        code, output = self.call("letters", "--id", "нет-такой")
        self.assertEqual(code, 1)
        self.assertIn("в отчёте нет", output)

    def test_without_report(self):
        code, output = self.call("letters")
        self.assertEqual(code, 1)
        self.assertIn("Сначала выполните run или check", output)


if __name__ == "__main__":
    unittest.main()
