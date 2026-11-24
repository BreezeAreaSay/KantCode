"""Клиент модели: любой сервер с OpenAI-совместимым ``/chat/completions``.

Подходит локальная Ollama (``http://localhost:11434/v1``, ключ не нужен) и
облачные провайдеры с таким же протоколом.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class AIError(RuntimeError):
    """Модель недоступна или ответила не JSON."""


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


class ChatClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._json_mode = True

    def _post(self, body: dict) -> dict:
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
            raise AIError(f"HTTP {error.code}: {detail}") from None
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
                if not str(error).startswith("HTTP 4"):
                    raise
                self._json_mode = False  # сервер не знает response_format: дальше просим JSON только словами
                reply = self._post(body)
        else:
            reply = self._post(body)
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError("в ответе сервера модели нет choices[0].message.content") from None
        return parse_json_object(content or "")
