import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import api_response, vacancy
from transparent_vacancy import store
from transparent_vacancy.store import Cache
from transparent_vacancy.textutil import clean_text, excerpt, find_quote
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



class MoreShapesTest(unittest.TestCase):
    def test_results_as_a_list_and_vacancies_without_results(self):
        self.assertEqual(len(extract_vacancies({"results": [{"vacancy": vacancy(id="a")}, {"vacancy": vacancy(id="b")}]})), 2)
        self.assertEqual(len(extract_vacancies({"vacancies": [{"vacancy": vacancy(id="a")}, vacancy(id="b")]})), 2)

    def test_objects_that_are_not_vacancies_are_skipped(self):
        self.assertEqual(extract_vacancies([{"status": "200"}, "строка", 5, None, {"vacancy": "не объект"}]), [])

    def test_term_as_plain_text(self):
        subject = Vacancy(vacancy(term="На время отпуска основного работника"))
        self.assertEqual(subject.get("term.text"), "На время отпуска основного работника")
        self.assertEqual(Vacancy(vacancy(term={"text": "на 3 месяца"})).get("term.text"), "на 3 месяца")

    def test_numbers_written_as_text(self):
        subject = Vacancy(vacancy(salary_min="45 000", salary_max="55000,5", requirement={"experience": True}))
        self.assertEqual((subject.number("salary_min"), subject.number("salary_max")), (45000.0, 55000.5))
        self.assertIsNone(subject.number("requirement.experience"))  # флаг это не число лет
        self.assertIsNone(Vacancy(vacancy(salary_min="по договорённости")).number("salary_min"))

    def test_zero_salary_bound_is_not_a_value(self):
        subject = Vacancy(vacancy(salary="", salary_min=35000, salary_max=0))
        self.assertEqual(subject.get("salary_min"), "35000")
        self.assertNotIn("salary_max", subject.fields)  # модель не увидит «0» как сумму
        self.assertNotIn("salary_min", Vacancy(vacancy(salary_min="0", salary_max=0.0)).fields)
        self.assertEqual(Vacancy(vacancy(requirement={"experience": 0})).get("requirement.experience"), "0")  # а опыт 0 это сведение

    def test_repeated_list_values_are_joined_once(self):
        subject = Vacancy(vacancy(shift=["Дневная смена", "Ночная смена", "Дневная смена", ""]))
        self.assertEqual(subject.get("shift"), "Дневная смена, Ночная смена")

    def test_fingerprint_follows_the_content_not_the_key_order(self):
        first = vacancy()
        second = dict(reversed(list(first.items())))
        self.assertEqual(Vacancy(first).fingerprint(), Vacancy(second).fingerprint())
        self.assertNotEqual(Vacancy(first).fingerprint(), Vacancy(vacancy(duty="Другой текст")).fingerprint())


class TextTest(unittest.TestCase):
    def test_find_quote_matches_whole_words_and_numbers_only(self):
        self.assertEqual(find_quote("35000", "0"), "")
        self.assertEqual(find_quote("Опытный пользователь ПК", "опыт"), "")
        self.assertEqual(find_quote("Оклад 35000 руб.", "35000"), "35000")
        self.assertEqual(find_quote("График 2/2, 08:00–20:00.", "2/2, 08:00–20:00"), "2/2, 08:00–20:00")

    def test_find_quote_of_nothing(self):
        self.assertEqual(find_quote("Возможны командировки", ""), "")
        self.assertEqual(find_quote("Возможны командировки", " .,;"), "")
        self.assertEqual(find_quote("", "командировки"), "")

    def test_excerpt_grows_to_whole_words_and_is_cut_by_the_limit(self):
        text = "Ведение документации, приём граждан и другие поручения."
        start = text.index("ругие")
        self.assertEqual(excerpt(text, start, start + 7), "другие поручения.")
        long_text = "слово " * 60
        piece = excerpt(long_text, 0, len(long_text), limit=40)
        self.assertLessEqual(len(piece), 40)
        self.assertTrue(piece.endswith("слово"))

    def test_clean_text_of_other_types(self):
        self.assertEqual([clean_text(None), clean_text(True), clean_text(False), clean_text(12)], ["", "да", "нет", "12"])
        self.assertEqual(clean_text("Оклад&nbsp;от 45&#160;000\u200b руб."), "Оклад от 45 000 руб.")


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_saved_entries_are_read_back(self):
        cache = Cache(self.dir / "deep" / "cache.json")
        cache.put("a", {"ai_findings": []})
        cache.save()
        self.assertEqual(Cache(self.dir / "deep" / "cache.json").get("a"), {"ai_findings": []})
        self.assertIsNone(Cache(self.dir / "deep" / "cache.json").get("b"))

    def test_oldest_entries_are_dropped_over_the_limit(self):
        path = self.dir / "cache.json"
        cache = Cache(path)
        with mock.patch.object(store, "CACHE_LIMIT", 2):
            for key in ("a", "b", "c"):
                cache.put(key, {"n": key})
            cache.put("a", {"n": "a2"})  # обновлённая запись становится самой свежей
            cache.save()
        self.assertEqual(list(Cache(path).entries), ["c", "a"])

    def test_nothing_is_written_without_changes_or_without_a_path(self):
        path = self.dir / "cache.json"
        Cache(path).save()
        self.assertFalse(path.exists())
        memory = Cache(None)
        memory.put("a", {})
        memory.save()  # кэш без файла живёт только в памяти

    def test_damaged_cache_file_is_treated_as_empty(self):
        path = self.dir / "cache.json"
        for text in ("{не json", "[1, 2]", '{"a": "не разбор"}'):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                self.assertIsNone(Cache(path).get("a"))


if __name__ == "__main__":
    unittest.main()
