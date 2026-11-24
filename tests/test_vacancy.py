import unittest

from tests.helpers import api_response, vacancy
from transparent_vacancy.textutil import clean_text, find_quote
from transparent_vacancy.vacancy import Vacancy, deduplicate, extract_vacancies


class ExtractTest(unittest.TestCase):
    def test_api_list_response(self):
        self.assertEqual(len(extract_vacancies(api_response([vacancy(), vacancy(id="2")]))), 2)

    def test_several_pages_in_one_file(self):
        pages = [api_response([vacancy()]), api_response([vacancy(id="2")])]
        self.assertEqual(len(extract_vacancies(pages)), 2)

    def test_bare_array_and_single_object(self):
        self.assertEqual(len(extract_vacancies([vacancy(), vacancy(id="2")])), 2)
        self.assertEqual(len(extract_vacancies({"vacancy": vacancy()})), 1)
        self.assertEqual(len(extract_vacancies(vacancy())), 1)

    def test_empty_results(self):
        self.assertEqual(extract_vacancies(api_response([])), [])
        self.assertEqual(extract_vacancies({"status": "500", "meta": {"error": "x"}}), [])


class FieldsTest(unittest.TestCase):
    def test_html_and_placeholders_are_cleaned(self):
        item = Vacancy(vacancy(duty="<p>Уборка&nbsp;помещений</p><ul><li>мытьё полов</li></ul>", qualification="Не указано"))
        self.assertEqual(item.get("duty"), "Уборка помещений мытьё полов")
        self.assertEqual(item.get("qualification"), "")

    def test_comparison_signs_are_not_tags(self):
        self.assertEqual(clean_text("стаж < 3 лет и > 1 года"), "стаж < 3 лет и > 1 года")

    def test_address_as_object_and_list(self):
        one = Vacancy(vacancy(addresses={"address": {"location": "г Калининград, Летняя улица, 3"}}))
        self.assertEqual(one.location_fields, ["addresses.address[0].location"])
        none = Vacancy(vacancy(addresses=None))
        self.assertEqual(none.location_fields, [])

    def test_id_and_url_may_be_absent(self):
        item = Vacancy(vacancy(id=None, vac_url=None))
        self.assertIsNone(item.id)
        self.assertIsNone(item.url)

    def test_find_quote_ignores_case_and_returns_source_text(self):
        self.assertEqual(find_quote("График: Гибкий график работы", "гибкий ГРАФИК"), "Гибкий график")
        self.assertEqual(find_quote("График сменный", "удобный график"), "")


class DeduplicateTest(unittest.TestCase):
    def test_one_result_per_id_latest_wins(self):
        old = vacancy(date_modify="2026-11-01T10:00:00+0300", salary_min=1)
        new = vacancy(date_modify="2026-11-20T10:00:00+0300", salary_min=2)
        kept, duplicates = deduplicate([Vacancy(new), Vacancy(old), Vacancy(vacancy(id="other"))])
        self.assertEqual(duplicates, 1)
        self.assertEqual([item.id for item in kept], [new["id"], "other"])
        self.assertEqual(kept[0].number("salary_min"), 2)

    def test_vacancies_without_id_are_all_kept(self):
        kept, duplicates = deduplicate([Vacancy(vacancy(id=None)), Vacancy(vacancy(id=None))])
        self.assertEqual((len(kept), duplicates), (2, 0))


if __name__ == "__main__":
    unittest.main()
