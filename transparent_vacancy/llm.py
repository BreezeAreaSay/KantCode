"""Клиент модели: любой сервер с OpenAI-совместимым ``/chat/completions``.

Подходит локальная Ollama (``http://localhost:11434/v1``, ключ не нужен) и
облачные провайдеры с таким же протоколом.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class AIError(RuntimeError):
    """Модель недоступна или ответила не JSON."""

    def __init__(self, message: str, code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code = code  # код HTTP, если сервер ответил ошибкой
        self.retry_after = retry_after  # сколько секунд сервер просит подождать


# Сервер занят или временно ограничил запросы: такой запрос стоит повторить.
RETRY_CODES = {408, 425, 429, 500, 502, 503, 504, 529}
# Сервер не понял запрос: так отвечают серверы, которые не знают response_format.
BAD_REQUEST_CODES = {400, 404, 415, 422}
MAX_PAUSE = 30.0


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in {"localhost", "127.0.0.1", "::1", "ollama", "host.docker.internal"}:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # локальной модели прокси не нужен
    return urllib.request.build_opener()


def parse_json_object(content: str) -> dict:
    """JSON-объект из ответа модели, даже если она обернула его в текст или ```json."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise AIError("в ответе модели нет JSON-объекта")
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError as error:
        raise AIError(f"ответ модели не разобран как JSON: {error}") from None
    if not isinstance(data, dict):
        raise AIError("ответ модели не является JSON-объектом")
    return data


def _retry_after(headers) -> float | None:
    try:
        return float(headers.get("Retry-After", ""))
    except (TypeError, ValueError, AttributeError):
        return None


class ChatClient:
    def __init__(
        self, base_url: str, model: str, api_key: str = "", timeout: int = 180, retries: int = 1, pause: float = 2.0
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = max(retries, 1)  # попыток на один запрос
        self.pause = pause  # секунд перед второй попыткой, дальше пауза растёт
        self._json_mode = True

    def _post(self, body: dict) -> dict:
        """Запрос с повторами: бесплатные и общие серверы часто отвечают «подождите» (429, 503)."""
        for attempt in range(1, self.retries + 1):
            try:
                return self._post_once(body)
            except AIError as error:
                temporary = error.code in RETRY_CODES or error.code is None and "нет связи" in str(error)
                if not temporary or attempt == self.retries:
                    raise
                wait = error.retry_after if error.retry_after is not None else self.pause * attempt
                time.sleep(min(max(wait, 0.0), MAX_PAUSE))
        raise AIError("модель не ответила")  # сюда не доходим: цикл либо возвращает ответ, либо бросает ошибку

    def _post_once(self, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"
        request = urllib.request.Request(
            url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with _opener(url).open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise AIError(f"HTTP {error.code}: {detail}", error.code, _retry_after(error.headers)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise AIError(f"нет связи с моделью ({self.base_url}): {getattr(error, 'reason', error)}") from None
        except ValueError as error:
            raise AIError(f"ответ сервера модели не разобран: {error}") from None

    def complete_json(self, system: str, user: str) -> dict:
        """Один запрос к модели, ответ как JSON-объект."""
        body = {
            "model": self.model,
            "temperature": 0,
            "seed": 7,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self._json_mode:
            try:
                reply = self._post({**body, "response_format": {"type": "json_object"}})
            except AIError as error:
                if error.code not in BAD_REQUEST_CODES:
                    raise  # перегрузка или лимит запросов: формат запроса тут ни при чём
                self._json_mode = False  # сервер не знает response_format: дальше просим JSON только словами
                reply = self._post(body)
        else:
            reply = self._post(body)
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError("в ответе сервера модели нет choices[0].message.content") from None
        return parse_json_object(content or "")
