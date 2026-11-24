"""Загрузка вакансий из API «Работа в России» постранично."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import __version__
from .vacancy import extract_vacancies


class SourceError(RuntimeError):
    """Источник недоступен или ответил не тем, что можно разобрать."""


@dataclass
class FetchResult:
    vacancies: list[dict] = field(default_factory=list)
    pages: int = 0
    total: int | None = None  # сколько вакансий всего, по словам источника


def _page_url(base_url: str, page: int, limit: int) -> str:
    parts = urllib.parse.urlsplit(base_url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["offset"] = str(page)  # у портала offset это номер страницы, а не число пропущенных записей
    query["limit"] = str(limit)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _get_json(url: str, timeout: int, retries: int, pause: float) -> dict:
    last_error = "неизвестная ошибка"
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": f"transparent-vacancy/{__version__}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("ответ не является JSON-объектом")
            status = str(payload.get("status", "200"))
            if status != "200":
                detail = (payload.get("meta") or {}).get("error") or "без пояснения"
                raise ValueError(f"источник вернул статус {status}: {detail}")
            return payload
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = f"нет связи: {getattr(error, 'reason', error)}"
        except (ValueError, UnicodeDecodeError) as error:
            last_error = f"ответ не разобран: {error}"
        if attempt < retries:
            time.sleep(pause * attempt)
    raise SourceError(f"{url}: {last_error} (попыток: {retries})")


def fetch_vacancies(
    base_url: str,
    max_vacancies: int,
    page_size: int,
    timeout: int = 60,
    retries: int = 3,
    pause: float = 2.0,
) -> FetchResult:
    """Читает страницы подряд, пока не наберёт ``max_vacancies`` или источник не закончится.

    Сбой на любой странице отменяет весь запуск: отчёт по половине данных заменил бы
    прошлый полный отчёт.
    """
    result = FetchResult()
    page = 0
    while len(result.vacancies) < max_vacancies:
        payload = _get_json(_page_url(base_url, page, page_size), timeout, retries, pause)
        items = extract_vacancies(payload)
        result.pages += 1
        meta = payload.get("meta") or {}
        total = meta.get("total") if isinstance(meta, dict) else None
        if isinstance(total, str) and total.strip().isdigit():
            total = int(total)  # документация показывает total строкой, портал отдаёт число
        if isinstance(total, int) and not isinstance(total, bool):
            result.total = total
        result.vacancies.extend(items)
        if len(items) < page_size:
            break  # последняя страница
        if result.total is not None and (page + 1) * page_size >= result.total:
            break
        page += 1
    del result.vacancies[max_vacancies:]
    return result
