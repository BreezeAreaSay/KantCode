import json
import re
import unittest

from tests.helpers import vacancy
from tests.test_cli import CliCase
from transparent_vacancy.analyzer import analyze_vacancies, build_report
from transparent_vacancy.config import Settings
from transparent_vacancy.html_report import render_html


def embedded(page: str) -> dict:
    body = re.search(r'<script id="report" type="application/json">(.*?)</script>', page, re.S).group(1)
    return json.loads(body)


class HtmlTest(unittest.TestCase):
    def report(self, *raw) -> dict:
        settings = Settings()
        settings.ai_enabled = False
        entries, ai, duplicates = analyze_vacancies(list(raw), settings)
        return build_report(entries, ai, "file", "test.json", len(raw), duplicates)

    def test_page_embeds_the_whole_report(self):
        report = self.report(vacancy(), vacancy(id="b", employment=None))
        self.assertEqual(embedded(render_html(report)), report)

    def test_markup_in_vacancy_text_cannot_break_out_of_the_data_block(self):
        hostile = 'Монтаж</script><script>alert(1)</script><!-- и другие поручения'
        report = self.report(vacancy(job__name=hostile, employment=None))
        page = render_html(report)
        self.assertEqual(page.count("</script>"), 2)  # только два своих блока
        self.assertNotIn("<script>alert", page)
        self.assertEqual(embedded(page)["vacancies"][0]["job_name"], report["vacancies"][0]["job_name"])

    def test_page_never_inserts_data_as_markup(self):
        self.assertNotIn("innerHTML", render_html(self.report(vacancy())))


class HtmlCommandTest(CliCase):
    def test_html_next_to_the_report(self):
        self.call("run", "--no-ai")
        code, _ = self.call("html")
        self.assertEqual(code, 0)
        page = (self.dir / "reports" / "report.html").read_text(encoding="utf-8")
        self.assertEqual(len(embedded(page)["vacancies"]), 6)

    def test_without_report(self):
        code, output = self.call("html")
        self.assertEqual(code, 1)
        self.assertIn("Сначала выполните run или check", output)


if __name__ == "__main__":
    unittest.main()
