"""Общие заготовки тестов: образцовая вакансия и локальные серверы вместо портала и модели."""

from __future__ import annotations

import copy
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

# Вакансия, в которой всё указано конкретно: правила не должны найти в ней ничего.
CLEAN = {
    "id": "11111111-aaaa-bbbb-cccc-000000000001",
    "source": "Вакансия работодателя",
    "company": {"companycode": "1023900000001", "name": "ГБУЗ «Городская больница № 1»"},
    "creation-date": "2026-11-01",
    "date_modify": "2026-11-20T10:00:00+0300",
    "salary": "от 45000",
    "salary_min": 45000,
    "salary_max": 55000,
    "job-name": "Медицинская сестра процедурной",
    "vac_url": "https://trudvsem.ru/vacancy/card/1023900000001/11111111-aaaa-bbbb-cccc-000000000001",
    "employment": "Полная занятость",
    "schedule": "Полный рабочий день",
    "duty": "Выполнение назначений врача, проведение инъекций и забор крови, ведение медицинской документации.",
    "requirements": "Среднее профессиональное образование по специальности «Сестринское дело», действующая аккредитация.",
    "qualification": "Медицинская сестра",
    "requirement": {"education": "Среднее профессиональное образование", "experience": 1},
    "addresses": {"address": [{"location": "Калининградская область, г Калининград, Летняя улица, дом: 3;"}]},
    "shift": [],
    "skills": [],
    "currency": "«руб.»",
}


# Без типа занятости, и график его не подсказывает: правила сообщают пропуск по занятости.
NO_TYPE = {"employment": None, "schedule": "Сменная работа: 2/2, 08:00–20:00"}


def vacancy(**changes) -> dict:
    """Копия образцовой вакансии с заменами. Значение ``None`` удаляет поле."""
    data = copy.deepcopy(CLEAN)
    for key, value in changes.items():
        key = key.replace("__", "-")
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def api_response(vacancies: list[dict], total: int | None = None) -> dict:
    return {
        "status": "200",
        "request": {"api": "v1"},
        "meta": {"total": len(vacancies) if total is None else total, "limit": 100},
        "results": {"vacancies": [{"vacancy": item} for item in vacancies]} if vacancies else {},
    }


class _Server:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class FakePortal(_Server):
    """Портал: отдаёт вакансии страницами, по запросу отвечает ошибкой."""

    def __init__(self, vacancies: list[dict]):
        self.vacancies = vacancies
        self.requests: list[dict] = []
        self.fail_with: int | None = None  # HTTP-код, которым отвечать вместо данных
        self.fail_from_page: int | None = None
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                query = parse_qs(urlsplit(self.path).query)
                page = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["100"])[0])
                portal.requests.append({"page": page, "limit": limit})
                broken = portal.fail_with is not None and (
                    portal.fail_from_page is None or page >= portal.fail_from_page
                )
                if broken:
                    body = {"status": str(portal.fail_with), "meta": {"error": "сбой портала"}}
                    self.send_response(portal.fail_with)
                else:
                    chunk = portal.vacancies[page * limit : (page + 1) * limit]
                    body = api_response(chunk, total=len(portal.vacancies))
                    self.send_response(200)
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(Handler)


class FakeModel(_Server):
    """Сервер модели с OpenAI-совместимым ответом. ``reply`` — функция от полей вакансии."""

    def __init__(self, reply=lambda fields: []):
        self.reply = reply
        self.calls = 0  # запросы по вакансиям
        self.probes = 0  # проверки связи перед разбором
        self.fail = False
        self.fail_models: set[str] = set()  # модели, которые отвечают «занято»
        self.asked: list[str] = []  # какая модель названа в каждом запросе по вакансии
        model = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                probe = "response_format" not in body and len(body["messages"]) == 1
                if probe:
                    model.probes += 1
                else:
                    model.calls += 1
                    model.asked.append(body.get("model"))
                if model.fail or body.get("model") in model.fail_models:
                    self.send_response(503)
                    self.end_headers()
                    return
                if probe:
                    content = "ok"
                else:
                    request = json.loads(body["messages"][-1]["content"])
                    content = json.dumps({"findings": model.reply(request["fields"])}, ensure_ascii=False)
                data = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(Handler)
