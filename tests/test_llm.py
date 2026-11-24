import json
import unittest
from http.server import BaseHTTPRequestHandler

from tests.helpers import _Server
from transparent_vacancy import llm
from transparent_vacancy.llm import AIError, ChatClient, parse_json_object

ANSWER = {"findings": []}


class ScriptedModel(_Server):
    """Сервер модели, который отвечает по сценарию: список (код, тело) на каждый запрос по очереди."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[dict] = []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                model.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
                code, payload = model.script.pop(0) if len(model.script) > 1 else model.script[0]
                data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(Handler)


def chat(content) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class ParseTest(unittest.TestCase):
    def test_object_inside_text_or_code_fence(self):
        self.assertEqual(parse_json_object('Вот ответ:\n```json\n{"findings": []}\n```'), ANSWER)

    def test_no_object_or_broken_object(self):
        for content in ("", "замечаний нет", "} {", '{"findings": [}'):
            with self.subTest(content=content):
                with self.assertRaises(AIError):
                    parse_json_object(content)


class ChatClientTest(unittest.TestCase):
    def serve(self, *script) -> tuple[ScriptedModel, ChatClient]:
        model = ScriptedModel(*script)
        self.addCleanup(model.close)
        return model, ChatClient(model.url + "/v1/", "stub", timeout=5)

    def test_request_shape(self):
        model, client = self.serve((200, chat(json.dumps(ANSWER))))
        self.assertEqual(client.complete_json("правила", "вакансия"), ANSWER)
        (request,) = model.requests
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertIsNone(request["authorization"])  # без ключа заголовок не отправляется
        body = request["body"]
        self.assertEqual((body["model"], body["temperature"], body["response_format"]), ("stub", 0, {"type": "json_object"}))
        self.assertEqual(
            body["messages"], [{"role": "system", "content": "правила"}, {"role": "user", "content": "вакансия"}]
        )

    def test_key_is_sent_as_bearer_token(self):
        model = ScriptedModel((200, chat(json.dumps(ANSWER))))
        self.addCleanup(model.close)
        ChatClient(model.url + "/v1", "stub", api_key="sk-test-123", timeout=5).complete_json("s", "u")
        self.assertEqual(model.requests[0]["authorization"], "Bearer sk-test-123")

    def test_server_without_json_mode_is_asked_in_words_from_then_on(self):
        model, client = self.serve((400, {"error": "response_format is not supported"}), (200, chat(json.dumps(ANSWER))))
        self.assertEqual(client.complete_json("s", "u"), ANSWER)
        self.assertEqual(client.complete_json("s", "u"), ANSWER)
        self.assertEqual(["response_format" in request["body"] for request in model.requests], [True, False, False])

    def test_server_error_is_not_retried_without_json_mode(self):
        model, client = self.serve((503, {"error": "перегрузка"}))
        with self.assertRaises(AIError) as caught:
            client.complete_json("s", "u")
        self.assertIn("HTTP 503", str(caught.exception))
        self.assertEqual(len(model.requests), 1)

    def test_answer_without_choices(self):
        for payload in ({}, {"choices": []}, {"choices": [{"message": None}]}):
            with self.subTest(payload=payload):
                _, client = self.serve((200, payload))
                with self.assertRaises(AIError) as caught:
                    client.complete_json("s", "u")
                self.assertIn("choices", str(caught.exception))

    def test_empty_or_non_json_content(self):
        for content in (None, "", "Замечаний нет."):
            with self.subTest(content=content):
                _, client = self.serve((200, chat(content)))
                with self.assertRaises(AIError):
                    client.complete_json("s", "u")

    def test_body_that_is_not_json(self):
        _, client = self.serve((200, b"<html>502 Bad Gateway</html>"))
        with self.assertRaises(AIError) as caught:
            client.complete_json("s", "u")
        self.assertIn("не разобран", str(caught.exception))

    def test_unreachable_server(self):
        model, client = self.serve((200, chat("{}")))
        model.close()
        with self.assertRaises(AIError) as caught:
            client.complete_json("s", "u")
        self.assertIn("нет связи с моделью", str(caught.exception))


class OpenerTest(unittest.TestCase):
    def proxies(self, url: str) -> dict | None:
        for handler in llm._opener(url).handlers:
            if handler.__class__.__name__ == "ProxyHandler":
                return handler.proxies
        return None

    def test_local_model_is_reached_without_a_proxy(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.example:3128", "https_proxy": "http://proxy.example:3128"}):
            for url in ("http://localhost:11434/v1/chat/completions", "http://127.0.0.1:8080/v1", "http://ollama:11434/v1"):
                with self.subTest(url=url):
                    self.assertFalse(self.proxies(url))
            self.assertTrue(self.proxies("https://openrouter.ai/api/v1/chat/completions"))


if __name__ == "__main__":
    unittest.main()
