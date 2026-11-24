import json
import os
import unittest
from http.server import BaseHTTPRequestHandler
from unittest import mock

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
                code, payload, *extra = model.script.pop(0) if len(model.script) > 1 else model.script[0]
                data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                for name, value in (extra[0] if extra else {}).items():
                    self.send_header(name, value)
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


class RetryTest(unittest.TestCase):
    BUSY = (429, {"error": {"message": "temporarily rate-limited upstream"}})

    def serve(self, *script, retries: int = 3) -> tuple[ScriptedModel, ChatClient]:
        model = ScriptedModel(*script)
        self.addCleanup(model.close)
        return model, ChatClient(model.url + "/v1", "stub", timeout=5, retries=retries, pause=0)

    def test_busy_server_is_asked_again(self):
        model, client = self.serve(self.BUSY, (503, {}), (200, chat(json.dumps(ANSWER))))
        self.assertEqual(client.complete_json("s", "u"), ANSWER)
        self.assertEqual(len(model.requests), 3)

    def test_rate_limit_does_not_switch_json_mode_off(self):
        model, client = self.serve(self.BUSY, (200, chat(json.dumps(ANSWER))))
        client.complete_json("s", "u")
        client.complete_json("s", "u")
        self.assertTrue(all("response_format" in request["body"] for request in model.requests))

    def test_gives_up_after_the_last_attempt(self):
        model, client = self.serve(self.BUSY, retries=2)
        with self.assertRaises(AIError) as caught:
            client.complete_json("s", "u")
        self.assertEqual(caught.exception.code, 429)
        self.assertIn("HTTP 429", str(caught.exception))
        self.assertEqual(len(model.requests), 2)

    def test_refused_request_is_not_repeated(self):
        model, client = self.serve((401, {"error": {"message": "No auth credentials found"}}))
        with self.assertRaises(AIError) as caught:
            client.complete_json("s", "u")
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(len(model.requests), 1)

    def test_spent_daily_limit_is_not_retried(self):
        spent = {"error": {"message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day", "code": 429}}
        model, client = self.serve((429, spent))
        with self.assertRaises(AIError) as caught:
            client.complete_json("s", "u")
        self.assertIn("free-models-per-day", str(caught.exception))
        self.assertEqual(len(model.requests), 1)  # повторы только потратили бы попытки

    def test_pause_grows_and_follows_the_server_hint(self):
        model = ScriptedModel(self.BUSY, (429, {}, {"Retry-After": "7"}), (429, {}, {"Retry-After": "600"}), (200, chat("{}")))
        self.addCleanup(model.close)
        client = ChatClient(model.url + "/v1", "stub", timeout=5, retries=4, pause=2)
        with mock.patch.object(llm.time, "sleep") as sleep:
            client.complete_json("s", "u")
        # без подсказки пауза растёт с номером попытки; подсказка сервера важнее, но не дольше полуминуты
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 7.0, llm.MAX_PAUSE])

    def test_lost_connection_is_retried_too(self):
        model, client = self.serve((200, chat("{}")), retries=2)
        model.close()
        with self.assertRaises(AIError) as caught:
            client.complete_json("s", "u")
        self.assertIsNone(caught.exception.code)
        self.assertIn("нет связи с моделью", str(caught.exception))


class ModelsByName(_Server):
    """Сервер с несколькими моделями: у каждой свой ответ, заданный как (код, тело)."""

    def __init__(self, **answers):
        self.answers = answers  # имя модели → (код, тело)
        self.asked: list[str] = []
        self.keys: list[str | None] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                server.asked.append(body["model"])
                server.keys.append(self.headers.get("Authorization"))
                code, payload = server.answers.get(body["model"], (404, {"error": {"message": "No endpoints found"}}))
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(Handler)


BUSY = (429, {"error": {"message": "temporarily rate-limited upstream"}})
SPENT = (429, {"error": {"message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock"}})
GOOD = (200, chat(json.dumps(ANSWER)))


class FallbackTest(unittest.TestCase):
    def serve(self, retries: int = 1, fallbacks=("backup",), **answers) -> tuple[ModelsByName, ChatClient]:
        server = ModelsByName(**answers)
        self.addCleanup(server.close)
        self.messages: list[str] = []
        client = ChatClient(
            server.url + "/v1", "main", api_key="sk-test", timeout=5, retries=retries, pause=0,
            fallbacks=fallbacks, cooldown=60, log=self.messages.append,
        )
        return server, client

    def test_busy_model_passes_the_request_to_the_next_one(self):
        server, client = self.serve(main=BUSY, backup=GOOD)
        with mock.patch.object(llm.time, "sleep") as sleep:
            self.assertEqual(client.ask("s", "u"), (ANSWER, "backup"))
        self.assertEqual(server.asked, ["main", "backup"])
        sleep.assert_not_called()  # ждать незачем: ответила запасная
        self.assertEqual(len(self.messages), 1)
        self.assertIn("модель main не ответила (HTTP 429), запрос уходит модели backup", self.messages[0])

    def test_busy_model_is_left_alone_for_a_while_and_then_tried_again(self):
        server, client = self.serve(main=BUSY, backup=GOOD)
        clock = [1000.0]
        with mock.patch.object(llm.time, "monotonic", lambda: clock[0]):
            client.ask("s", "u")
            client.ask("s", "u")
            self.assertEqual(server.asked, ["main", "backup", "backup"])  # второй запрос сразу к запасной
            clock[0] += 61
            server.answers["main"] = GOOD
            self.assertEqual(client.ask("s", "u")[1], "main")  # основная отдохнула и снова первая

    def test_spent_daily_limit_and_unknown_model_are_dropped_for_the_whole_run(self):
        for answer in (SPENT, (404, {"error": {"message": "No endpoints found for main"}}), (401, {"error": "bad key"})):
            with self.subTest(code=answer[0], text=str(answer[1])[:40]):
                server, client = self.serve(main=answer, backup=GOOD)
                clock = [1000.0]
                with mock.patch.object(llm.time, "monotonic", lambda: clock[0]):
                    self.assertEqual(client.ask("s", "u")[1], "backup")
                    clock[0] += 3600
                    server.asked.clear()
                    self.assertEqual(client.ask("s", "u")[1], "backup")
                self.assertEqual(server.asked, ["backup"])

    def test_when_every_model_is_busy_the_round_is_repeated(self):
        server, client = self.serve(retries=2, main=BUSY, backup=BUSY)
        with self.assertRaises(AIError) as caught:
            client.ask("s", "u")
        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(server.asked, ["main", "backup", "main", "backup"])

    def test_when_every_model_is_spent_nothing_is_repeated(self):
        server, client = self.serve(retries=3, main=SPENT, backup=SPENT)
        with self.assertRaises(AIError):
            client.ask("s", "u")
        self.assertEqual(server.asked, ["main", "backup"])

    def test_answer_that_is_not_json_goes_to_the_next_model(self):
        server, client = self.serve(main=(200, chat("Замечаний нет.")), backup=GOOD)
        self.assertEqual(client.ask("s", "u"), (ANSWER, "backup"))
        client.ask("s", "u")
        # плохой ответ не повод откладывать модель: следующий запрос снова начинается с основной
        self.assertEqual(server.asked, ["main", "backup", "main", "backup"])

    def test_three_models_in_the_given_order(self):
        server, client = self.serve(fallbacks=("backup", "third", "backup", " "), main=BUSY, backup=BUSY, third=GOOD)
        self.assertEqual([target.label for target in client.targets], ["main", "backup", "third"])
        self.assertEqual(client.ask("s", "u")[1], "third")
        self.assertEqual(server.asked, ["main", "backup", "third"])

    def test_fallback_on_another_server_gets_no_key(self):
        local = ModelsByName(**{"qwen2.5:7b": GOOD})
        self.addCleanup(local.close)
        server, client = self.serve(fallbacks=(f"qwen2.5:7b@{local.url}/v1/",), main=SPENT)
        answer, model = client.ask("s", "u")
        self.assertEqual((answer, model), (ANSWER, f"qwen2.5:7b@{local.url}/v1"))
        self.assertEqual(server.keys, ["Bearer sk-test"])
        self.assertEqual(local.keys, [None])  # ключ основного сервера чужому серверу не отправляется

    def test_json_mode_is_remembered_for_each_model_separately(self):
        server = ScriptedModel((200, chat(json.dumps(ANSWER))))
        self.addCleanup(server.close)
        plain = ScriptedModel((400, {"error": "response_format is not supported"}), (200, chat(json.dumps(ANSWER))))
        self.addCleanup(plain.close)
        client = ChatClient(plain.url + "/v1", "old", timeout=5, fallbacks=(f"new@{server.url}/v1",))
        client.ask("s", "u")
        client.ask("s", "u")
        self.assertEqual(["response_format" in request["body"] for request in plain.requests], [True, False, False])
        client._gone.add(client.targets[0])
        client.ask("s", "u")
        self.assertIn("response_format", server.requests[0]["body"])


class ProbeTest(unittest.TestCase):
    def test_probe_tells_which_models_answer_and_sets_the_others_aside(self):
        server = ModelsByName(main=BUSY, backup=GOOD, spent=SPENT)
        self.addCleanup(server.close)
        client = ChatClient(server.url + "/v1", "main", timeout=5, fallbacks=("spent", "backup", "absent"))
        results = dict(client.probe())
        self.assertEqual(list(results), ["main", "spent", "backup", "absent"])
        self.assertEqual(results["backup"], "ok")
        self.assertIn("HTTP 429", results["main"])
        self.assertIn("free-models-per-day", results["spent"])
        self.assertIn("HTTP 404", results["absent"])
        server.asked.clear()
        self.assertEqual(client.ask("s", "u")[1], "backup")
        self.assertEqual(server.asked, ["backup"])  # к нерабочим моделям запрос по вакансии не уходит

    def test_probe_can_stop_at_the_first_model_that_answers(self):
        server = ModelsByName(main=BUSY, backup=GOOD, third=GOOD)
        self.addCleanup(server.close)
        client = ChatClient(server.url + "/v1", "main", timeout=5, fallbacks=("backup", "third"))
        self.assertEqual([label for label, _ in client.probe(first_only=True)], ["main", "backup"])
        self.assertEqual(server.asked, ["main", "backup"])  # до третьей модели очередь не дошла

    def test_probe_is_a_short_request_without_the_vacancy(self):
        server = ScriptedModel((200, chat("ok")))
        self.addCleanup(server.close)
        self.assertEqual(ChatClient(server.url + "/v1", "main", timeout=5).probe(), [("main", "ok")])
        body = server.requests[0]["body"]
        self.assertEqual((body["model"], body["max_tokens"], len(body["messages"])), ("main", 8, 1))
        self.assertNotIn("response_format", body)

    def test_answer_without_choices_is_not_a_working_model(self):
        server = ScriptedModel((200, {"error": "model is loading"}))
        self.addCleanup(server.close)
        ((label, state),) = ChatClient(server.url + "/v1", "main", timeout=5).probe()
        self.assertEqual(label, "main")
        self.assertIn("choices", state)

    def test_unreachable_server(self):
        server = ScriptedModel((200, chat("ok")))
        client = ChatClient(server.url + "/v1", "main", timeout=5)
        server.close()
        self.assertIn("нет связи с моделью", client.probe()[0][1])


class OpenerTest(unittest.TestCase):
    def proxies(self, url: str) -> dict | None:
        for handler in llm._opener(url).handlers:
            if handler.__class__.__name__ == "ProxyHandler":
                return handler.proxies
        return None

    def test_local_model_is_reached_without_a_proxy(self):
        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.example:3128", "https_proxy": "http://proxy.example:3128"}):
            for url in ("http://localhost:11434/v1/chat/completions", "http://127.0.0.1:8080/v1", "http://ollama:11434/v1"):
                with self.subTest(url=url):
                    self.assertFalse(self.proxies(url))
            self.assertTrue(self.proxies("https://openrouter.ai/api/v1/chat/completions"))


if __name__ == "__main__":
    unittest.main()
