"""Клиент модели: любой сервер с OpenAI-совместимым ``/chat/completions``.

Подходит локальная Ollama (``http://localhost:11434/v1``, ключ не нужен) и
облачные провайдеры с таким же протоколом.

Моделей может быть несколько: основная и запасные. Когда модель упирается в
лимит запросов или недоступна, клиент сам переходит на следующую и через
некоторое время пробует вернуться к основной.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Sequence


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
# Модель или ключ не подходят совсем: до конца запуска к такой модели не возвращаемся.
GONE_CODES = {401, 402, 403, 404}
MAX_PAUSE = 30.0
PROBE_MESSAGE = "Проверка связи. Ответь одним словом: ok"
COOLDOWN = 60.0  # секунд не трогаем модель, которая попросила подождать, если есть запасная
_QUOTA_SPENT = re.compile(r"per[- ]day|daily|quota|credits|insufficient", re.I)


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


@dataclass(frozen=True)
class Target:
    """Куда отправлять запрос: модель на конкретном сервере."""

    model: str
    base_url: str
    api_key: str = ""
    label: str = ""  # как модель названа в отчёте и логе


def parse_targets(base_url: str, model: str, api_key: str, fallbacks: Sequence[str]) -> list[Target]:
    """Основная модель и запасные. Запасная пишется ``модель`` или ``модель@адрес-сервера``.

    Ключ уходит только на основной сервер: чужому серверу он не нужен и не должен достаться.
    """
    base_url = base_url.rstrip("/")
    targets = [Target(model, base_url, api_key, model)]
    for item in fallbacks:
        name, _, url = item.strip().partition("@")
        name, url = name.strip(), url.strip().rstrip("/")
        if not name:
            continue
        if url and url != base_url:
            target = Target(name, url, "", f"{name}@{url}")
        else:
            target = Target(name, base_url, api_key, name)
        if target not in targets:
            targets.append(target)
    return targets


class ChatClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: int = 180,
        retries: int = 1,
        pause: float = 2.0,
        fallbacks: Sequence[str] = (),
        cooldown: float = COOLDOWN,
        log: Callable[[str], None] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = max(retries, 1)  # кругов по моделям на один запрос
        self.pause = pause  # секунд перед вторым кругом, дальше пауза растёт
        self.cooldown = cooldown
        self.log = log or (lambda message: None)
        self.targets = parse_targets(base_url, model, api_key, fallbacks)
        self._lock = threading.Lock()
        self._plain: set[Target] = set()  # серверы, которые не знают response_format
        self._resting: dict[Target, float] = {}  # модель попросила подождать: до какого момента её не трогаем
        self._gone: set[Target] = set()  # модель или ключ не подходят: до конца запуска

    # --- выбор модели ----------------------------------------------------

    def _candidates(self) -> list[Target]:
        """Модели, которые стоит спросить сейчас, в порядке предпочтения."""
        now = time.monotonic()
        with self._lock:
            alive = [target for target in self.targets if target not in self._gone] or list(self.targets)
            ready = [target for target in alive if self._resting.get(target, 0.0) <= now]
        return ready or alive  # отдыхают все: спрашиваем всё равно, по порядку

    def _set_aside(self, target: Target, error: AIError, temporary: bool, spent: bool) -> bool:
        """Запоминает, что модель сейчас не отвечает. Возвращает True, если это новость."""
        with self._lock:
            if spent or error.code in GONE_CODES:
                fresh = target not in self._gone
                self._gone.add(target)
            elif temporary:
                now = time.monotonic()
                fresh = self._resting.get(target, 0.0) <= now
                self._resting[target] = now + self.cooldown
            else:
                fresh = False
        return fresh

    # --- запрос ----------------------------------------------------------

    def _post_once(self, target: Target, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if target.api_key:
            headers["Authorization"] = f"Bearer {target.api_key}"
        url = f"{target.base_url}/chat/completions"
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
            raise AIError(f"нет связи с моделью ({target.base_url}): {getattr(error, 'reason', error)}") from None
        except ValueError as error:
            raise AIError(f"ответ сервера модели не разобран: {error}") from None

    def _ask_target(self, target: Target, system: str, user: str) -> dict:
        """Один запрос к одной модели, ответ как JSON-объект."""
        body = {
            "model": target.model,
            "temperature": 0,
            "seed": 7,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if target in self._plain:
            reply = self._post_once(target, body)
        else:
            try:
                reply = self._post_once(target, {**body, "response_format": {"type": "json_object"}})
            except AIError as error:
                if error.code not in BAD_REQUEST_CODES:
                    raise  # перегрузка или лимит запросов: формат запроса тут ни при чём
                with self._lock:
                    self._plain.add(target)  # сервер не знает response_format: дальше просим JSON только словами
                reply = self._post_once(target, body)
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError("в ответе сервера модели нет choices[0].message.content") from None
        return parse_json_object(content or "")

    def ask(self, system: str, user: str) -> tuple[dict, str]:
        """Ответ модели как JSON-объект и название модели, которая ответила.

        Модели спрашиваются по порядку. Если модель занята или упёрлась в лимит, запрос
        сразу уходит следующей; когда не ответила ни одна, круг повторяется после паузы.
        """
        last: AIError | None = None
        for attempt in range(1, self.retries + 1):
            candidates = self._candidates()
            worth_waiting = False
            for position, target in enumerate(candidates):
                try:
                    return self._ask_target(target, system, user), target.label
                except AIError as error:
                    last = error
                    spent = bool(_QUOTA_SPENT.search(str(error)))  # дневной лимит или пустой счёт
                    temporary = not spent and (
                        error.code in RETRY_CODES or error.code is None and "нет связи" in str(error)
                    )
                    worth_waiting = worth_waiting or temporary
                    fresh = self._set_aside(target, error, temporary, spent)
                    if fresh and position + 1 < len(candidates):
                        reason = f"HTTP {error.code}" if error.code else str(error)[:80]
                        self.log(f"ИИ: модель {target.label} не ответила ({reason}), запрос уходит модели {candidates[position + 1].label}")
            if not worth_waiting or attempt == self.retries:
                break  # ждать нечего: ошибка не временная или попытки кончились
            wait = last.retry_after if last and last.retry_after is not None else self.pause * attempt
            time.sleep(min(max(wait, 0.0), MAX_PAUSE))
        raise last or AIError("модель не ответила")

    def complete_json(self, system: str, user: str) -> dict:
        """Один запрос к модели, ответ как JSON-объект."""
        return self.ask(system, user)[0]

    def probe(self, first_only: bool = False) -> list[tuple[str, str]]:
        """Заранее проверяет коротким запросом, какие модели отвечают.

        Возвращает пары (модель, «ok» или текст ошибки). Модель, которая не ответила,
        откладывается: запросы по вакансиям пойдут сразу к рабочим. С ``first_only``
        проверка идёт по порядку до первой ответившей: запасные модели не тратят
        запросы и не загружаются в память, пока до них не дошла очередь.
        """
        results: list[tuple[str, str]] = []
        for target in self.targets:
            if first_only and results and results[-1][1] == "ok":
                break
            body = {
                "model": target.model,
                "temperature": 0,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": PROBE_MESSAGE}],
            }
            try:
                reply = self._post_once(target, body)
                if not isinstance(reply, dict) or not reply.get("choices"):
                    raise AIError("в ответе сервера модели нет choices")
                results.append((target.label, "ok"))
            except AIError as error:
                spent = bool(_QUOTA_SPENT.search(str(error)))
                temporary = not spent and (error.code in RETRY_CODES or error.code is None)
                self._set_aside(target, error, temporary, spent)
                results.append((target.label, str(error)))
        return results
