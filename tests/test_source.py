import unittest

from tests.helpers import FakePortal, vacancy
from transparent_vacancy.source import SourceError, fetch_vacancies


def many(count: int) -> list[dict]:
    return [vacancy(id=f"id-{number}") for number in range(count)]


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.portal = FakePortal(many(120))
        self.addCleanup(self.portal.close)

    def fetch(self, limit: int, page_size: int):
        return fetch_vacancies(self.portal.url + "/api/v1/vacancies/region/39", limit, page_size, 5, 2, 0)

    def test_two_pages_are_read_up_to_the_limit(self):
        result = self.fetch(100, 50)
        self.assertEqual((len(result.vacancies), result.pages, result.total), (100, 2, 120))
        self.assertEqual(self.portal.requests, [{"page": 0, "limit": 50}, {"page": 1, "limit": 50}])
        self.assertEqual(len({item["id"] for item in result.vacancies}), 100)

    def test_limit_cuts_the_last_page(self):
        result = self.fetch(70, 50)
        self.assertEqual((len(result.vacancies), result.pages), (70, 2))

    def test_stops_when_source_ends(self):
        result = self.fetch(500, 50)
        self.assertEqual((len(result.vacancies), result.pages), (120, 3))

    def test_empty_source_is_not_an_error(self):
        self.portal.vacancies = []
        result = self.fetch(100, 50)
        self.assertEqual((result.vacancies, result.pages, result.total), ([], 1, 0))

    def test_portal_error_is_reported_after_retries(self):
        self.portal.fail_with = 500
        with self.assertRaises(SourceError) as caught:
            self.fetch(100, 50)
        self.assertIn("HTTP 500", str(caught.exception))
        self.assertEqual(len(self.portal.requests), 2)  # две попытки

    def test_failure_on_second_page_fails_the_run(self):
        self.portal.fail_with, self.portal.fail_from_page = 500, 1
        with self.assertRaises(SourceError):
            self.fetch(100, 50)

    def test_unreachable_host(self):
        with self.assertRaises(SourceError) as caught:
            fetch_vacancies("http://127.0.0.1:9/api", 10, 10, 2, 1, 0)
        self.assertIn("нет связи", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
