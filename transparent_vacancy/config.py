"""Настройки: переменные окружения и файл ``.env`` рядом с запуском.

Ключи хранятся только в окружении или в ``.env`` (он в ``.gitignore``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SOURCE_URL = "http://opendata.trudvsem.ru/api/v1/vacancies/region/39"

# Локальная модель: Ollama на этой же машине (в Docker адрес подменяет TV_AI_LOCAL_URL).
LOCAL_URL = "http://localhost:11434/v1"
LOCAL_MODEL = "qwen2.5:7b"
# Если задан только ключ, это ключ OpenRouter: адрес и бесплатные модели подставляются сами.
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_FALLBACKS = ("nvidia/nemotron-3.5-lightning:free",)
OPENROUTER_TIMEOUT = 600  # отдельный ответ бесплатной модели занимал больше трёх минут
API_PAGE_LIMIT = 100  # больше портал за один запрос не отдаёт


class ConfigError(ValueError):
    """Настройка задана значением, с которым работать нельзя."""


def load_env_file(path: Path) -> dict[str, str]:
    """Читает ``KEY=VALUE`` из файла. Значения из окружения важнее и не перезаписываются."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _integer(name: str, raw: str, minimum: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name}: нужно целое число, получено «{raw}»") from None
    if value < minimum:
        raise ConfigError(f"{name}: значение не может быть меньше {minimum}")
    return value


def _flag(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


@dataclass
class Settings:
    source_url: str = DEFAULT_SOURCE_URL
    max_vacancies: int = 100
    page_size: int = 50
    schedule: str = "0 6 * * *"
    output_dir: Path = Path("reports")
    http_timeout: int = 60
    http_retries: int = 3
    ai_enabled: bool = True
    ai_base_url: str = LOCAL_URL
    ai_model: str = LOCAL_MODEL
    ai_fallback_models: tuple[str, ...] = ()  # запасные модели: «модель» или «модель@адрес-сервера»
    ai_api_key: str = ""
    ai_timeout: int = 180
    ai_retries: int = 3  # попыток на один запрос к модели: бесплатные серверы часто просят подождать
    ai_workers: int = 2
    ai_max_vacancies: int = 0  # 0 — без ограничения
    ai_strict: bool = True  # неоднозначность от модели только с признаком расплывчатости в цитате

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = ".env") -> "Settings":
        values = load_env_file(Path(env_file)) if env_file else {}
        values.update({key: value for key, value in os.environ.items() if key.startswith("TV_")})

        def text(name: str, default: str) -> str:
            return values.get(name, "").strip() or default

        def given(name: str) -> bool:
            return bool(values.get(name, "").strip())

        base = cls()
        # Достаточно одного ключа: без адреса сервера он считается ключом OpenRouter.
        by_token = given("TV_AI_API_KEY") and not given("TV_AI_BASE_URL")
        if by_token:
            default_url, default_model, default_timeout = OPENROUTER_URL, OPENROUTER_MODEL, OPENROUTER_TIMEOUT
        else:
            default_url = text("TV_AI_LOCAL_URL", base.ai_base_url)
            default_model = text("TV_AI_LOCAL_MODEL", base.ai_model)
            default_timeout = base.ai_timeout
        if by_token and not given("TV_AI_MODEL"):
            # Цепочка на случай лимитов: бесплатная модель, запасная, затем локальная, если она запущена.
            local = f"{text('TV_AI_LOCAL_MODEL', base.ai_model)}@{text('TV_AI_LOCAL_URL', base.ai_base_url).rstrip('/')}"
            default_fallbacks = ",".join(OPENROUTER_FALLBACKS + (local,))
        else:
            default_fallbacks = ""
        settings = cls(
            source_url=text("TV_SOURCE_URL", base.source_url),
            max_vacancies=_integer("TV_MAX_VACANCIES", text("TV_MAX_VACANCIES", str(base.max_vacancies)), 1),
            page_size=_integer("TV_PAGE_SIZE", text("TV_PAGE_SIZE", str(base.page_size)), 1),
            schedule=text("TV_SCHEDULE", base.schedule),
            output_dir=Path(text("TV_OUTPUT_DIR", str(base.output_dir))),
            http_timeout=_integer("TV_HTTP_TIMEOUT", text("TV_HTTP_TIMEOUT", str(base.http_timeout)), 1),
            http_retries=_integer("TV_HTTP_RETRIES", text("TV_HTTP_RETRIES", str(base.http_retries)), 1),
            ai_enabled=_flag(text("TV_AI_ENABLED", "true")),
            ai_base_url=text("TV_AI_BASE_URL", default_url).rstrip("/"),
            ai_model=text("TV_AI_MODEL", default_model),
            ai_fallback_models=tuple(
                item.strip() for item in text("TV_AI_FALLBACK_MODELS", default_fallbacks).split(",") if item.strip()
            ),
            ai_api_key=text("TV_AI_API_KEY", ""),
            ai_timeout=_integer("TV_AI_TIMEOUT", text("TV_AI_TIMEOUT", str(default_timeout)), 1),
            ai_retries=_integer("TV_AI_RETRIES", text("TV_AI_RETRIES", str(base.ai_retries)), 1),
            ai_workers=_integer("TV_AI_WORKERS", text("TV_AI_WORKERS", str(base.ai_workers)), 1),
            ai_max_vacancies=_integer("TV_AI_MAX_VACANCIES", text("TV_AI_MAX_VACANCIES", "0"), 0),
            ai_strict=_flag(text("TV_AI_STRICT", "true")),
        )
        if not settings.source_url.lower().startswith(("http://", "https://")):
            raise ConfigError("TV_SOURCE_URL: нужен адрес, начинающийся с http:// или https://")
        settings.page_size = min(settings.page_size, API_PAGE_LIMIT)
        return settings
