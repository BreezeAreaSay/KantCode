import json
import unittest
from http.server import BaseHTTPRequestHandler

from tests.helpers import FakePortal, _Server, api_response, vacancy
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



class RawPortal(_Server):
    """Портал, который на любой запрос отдаёт заданные байты."""

    def __init__(self, body: bytes, code: int = 200):
        self.calls = 0
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                portal.calls += 1
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        super().__init__(Handler)


class BadAnswerTest(unittest.TestCase):
    def fetch(self, payload, retries: int = 2):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.portal = RawPortal(body)
        self.addCleanup(self.portal.close)
        return fetch_vacancies(self.portal.url + "/api/v1/vacancies/region/39", 10, 5, 5, retries, 0)

    def test_error_status_inside_a_successful_http_answer(self):
        with self.assertRaises(SourceError) as caught:
            self.fetch({"status": "500", "meta": {"error": "слишком далёкая страница"}})
        self.assertIn("источник вернул статус 500: слишком далёкая страница", str(caught.exception))
        self.assertEqual(self.portal.calls, 2)  # обе попытки использованы

    def test_error_status_without_explanation(self):
        with self.assertRaises(SourceError) as caught:
            self.fetch({"status": "404"}, retries=1)
        self.assertIn("без пояснения", str(caught.exception))

    def test_answer_that_is_not_an_object_or_not_json(self):
        for body in ([1, 2, 3], b"<html>Bad Gateway</html>", b"\xff\xfe\x00"):
            with self.subTest(body=body):
                with self.assertRaises(SourceError) as caught:
                    self.fetch(body, retries=1)
                self.assertIn("ответ не разобран", str(caught.exception))

    def test_total_as_a_string_like_in_the_documentation(self):
        answer = api_response([vacancy(id="a"), vacancy(id="b")])
        answer["meta"] = {"total": "2", "limit": "5", "offset": "0"}
        result = self.fetch(answer)
        self.assertEqual((len(result.vacancies), result.pages, result.total), (2, 1, 2))

    def test_query_of_the_source_address_is_kept(self):
        from transparent_vacancy.source import _page_url

        url = _page_url("http://portal.example/api/v1/vacancies/region/39?text=врач&limit=1", 3, 50)
        self.assertEqual(url, "http://portal.example/api/v1/vacancies/region/39?text=%D0%B2%D1%80%D0%B0%D1%87&limit=50&offset=3")


if __name__ == "__main__":
    unittest.main()
