"""Анализатор: один и тот же для рабочего и проверочного режима.

Вход — вакансии в формате API, выход — отчёт. Откуда вакансии пришли (портал или
локальный файл), анализатору безразлично.
"""

from __future__ import annotations

import hashlib
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from . import __version__, ai_review, rules
from .config import Settings
from .findings import Finding
from .llm import AIError, ChatClient
from .store import Cache
from .vacancy import Vacancy, deduplicate

RULES_VERSION = "3"
FAILURES_TO_STOP = 3  # столько сбоев модели подряд, и остальные вакансии идут без неё


@dataclass
class AIRun:
    """Итог работы модели за запуск: попадает в meta.ai отчёта."""

    enabled: bool
    base_url: str = ""
    model: str = ""
    fallback_models: list = field(default_factory=list)
    models_checked: dict = field(default_factory=dict)  # модель → «ok» или ошибка проверки перед разбором
    models_used: dict = field(default_factory=dict)  # модель → сколько вакансий отчёта она разобрала
    reviewed: int = 0
    from_cache: int = 0
    failed: int = 0
    skipped: int = 0
    findings: int = 0
    error: str = ""
    _streak: int = field(default=0, repr=False)
    _stopped: bool = field(default=False, repr=False)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        done = self.reviewed + self.from_cache
        if self.failed == 0 and self.skipped == 0:
            return "ok"
        return "partial" if done else "unavailable"

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "base_url": self.base_url,
            "model": self.model,
            "fallback_models": list(self.fallback_models),
            "models_checked": dict(self.models_checked),
            "models_used": dict(self.models_used),
            "vacancies_reviewed": self.reviewed,
            "vacancies_from_cache": self.from_cache,
            "vacancies_failed": self.failed,
            "vacancies_skipped": self.skipped,
            "findings": self.findings,
            "error": self.error,
        }


def build_client(settings: Settings, log: Callable[[str], None] = lambda message: None) -> ChatClient:
    """Клиент модели по настройкам: основная модель и запасные."""
    return ChatClient(
        settings.ai_base_url,
        settings.ai_model,
        settings.ai_api_key,
        settings.ai_timeout,
        settings.ai_retries,
        fallbacks=settings.ai_fallback_models,
        log=log,
    )


def _cache_key(vacancy: Vacancy, ai_signature: str) -> str:
    body = "|".join((vacancy.fingerprint(), RULES_VERSION, ai_signature))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _entry(vacancy: Vacancy, findings: list[Finding], ai_reviewed: bool, ai_model: str | None = None) -> dict:
    return {
        "id": vacancy.id,
        "url": vacancy.url,
        "job_name": vacancy.title,
        "company": vacancy.company,
        "ai_reviewed": ai_reviewed,
        "ai_model": ai_model if ai_reviewed else None,
        "findings": [item.to_json() for item in findings],
    }


def analyze_vacancies(
    raw_vacancies: list[dict],
    settings: Settings,
    cache: Cache | None = None,
    client: ChatClient | None = None,
    log: Callable[[str], None] = lambda message: None,
) -> tuple[list[dict], AIRun, int]:
    """Разбирает вакансии. Возвращает записи отчёта, итог работы модели и число склеенных дублей."""
    vacancies, duplicates = deduplicate(
        Vacancy(raw, index) for index, raw in enumerate(raw_vacancies) if isinstance(raw, dict)
    )
    cache = cache or Cache(None)
    ai = AIRun(
        enabled=settings.ai_enabled,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        fallback_models=list(settings.ai_fallback_models),
    )
    if settings.ai_enabled and client is None:
        client = build_client(settings, log)
    signature = (
        f"{settings.ai_model}|{ai_review.PROMPT_VERSION}|{settings.ai_strict}" if settings.ai_enabled else "no-ai"
    )
    lock = threading.Lock()
    probe_lock = threading.Lock()
    probed = [False]
    budget = settings.ai_max_vacancies or len(vacancies)
    order = {id(vacancy): position for position, vacancy in enumerate(vacancies)}

    def check_models() -> None:
        """Перед первым запросом по вакансии выясняет, какие модели отвечают. Делается один раз."""
        with probe_lock:
            if probed[0] or not hasattr(client, "probe"):
                return
            probed[0] = True
            results = client.probe()
            for label, state in results:
                ai.models_checked[label] = state
                log(f"ИИ: проверка модели {label}: {'отвечает' if state == 'ok' else 'не отвечает — ' + state[:160]}")
            if results and all(state != "ok" for _, state in results):
                with lock:
                    ai._stopped = True
                    ai.error = results[-1][1]
                log("ИИ: ни одна модель не ответила, вакансии разбираются только правилами")

    def process(vacancy: Vacancy) -> dict:
        rule_findings, facts = rules.analyze(vacancy)
        key = _cache_key(vacancy, signature)
        if not settings.ai_enabled:
            return _entry(vacancy, rule_findings, False)
        cached = cache.get(key)
        if cached is not None:
            model = cached.get("model") or settings.ai_model
            with lock:
                ai.from_cache += 1
                ai.findings += len(cached.get("ai_findings", []))
                ai.models_used[model] = ai.models_used.get(model, 0) + 1
            extra = [Finding.from_json(item) for item in cached.get("ai_findings", [])]
            return _entry(vacancy, rule_findings + extra, True, model)
        if order[id(vacancy)] < budget:
            check_models()
        with lock:
            allowed = not ai._stopped and order[id(vacancy)] < budget
            if not allowed:
                ai.skipped += 1
        if not allowed:
            return _entry(vacancy, rule_findings, False)
        try:
            extra, model = ai_review.review(client, vacancy, rule_findings, facts, settings.ai_strict)
        except AIError as error:
            with lock:
                ai.failed += 1
                ai.error = str(error)
                ai._streak += 1
                if ai._streak >= FAILURES_TO_STOP and not ai._stopped:
                    ai._stopped = True
                    log(f"ИИ: {FAILURES_TO_STOP} сбоя подряд, остальные вакансии разбираются только правилами")
            return _entry(vacancy, rule_findings, False)
        with lock:
            ai._streak = 0
            ai.reviewed += 1
            ai.findings += len(extra)
            ai.models_used[model] = ai.models_used.get(model, 0) + 1
            cache.put(key, {"ai_findings": [item.to_json() for item in extra], "model": model})
            done = ai.reviewed
        if done % 10 == 0:
            log(f"ИИ: разобрано вакансий: {done}")
        return _entry(vacancy, rule_findings + extra, True, model)

    if settings.ai_enabled and settings.ai_workers > 1 and len(vacancies) > 1:
        with ThreadPoolExecutor(max_workers=settings.ai_workers) as pool:
            entries = list(pool.map(process, vacancies))
    else:
        entries = [process(vacancy) for vacancy in vacancies]
    return entries, ai, duplicates


def build_report(
    entries: list[dict],
    ai: AIRun,
    mode: str,
    source: str,
    received: int,
    duplicates: int,
    extra_meta: dict | None = None,
) -> dict:
    by_topic: Counter = Counter()
    by_type: Counter = Counter()
    for entry in entries:
        for item in entry["findings"]:
            by_topic[item["topic"]] += 1
            by_type[item["type"]] += 1
    meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "tool_version": __version__,
        "mode": mode,
        "source": source,
        "vacancies_received": received,
        "duplicates_merged": duplicates,
        "vacancies": len(entries),
        "vacancies_with_findings": sum(1 for entry in entries if entry["findings"]),
        "findings_total": sum(by_type.values()),
        "findings_by_topic": dict(sorted(by_topic.items())),
        "findings_by_type": dict(sorted(by_type.items())),
        "ai": ai.to_json(),
    }
    meta.update(extra_meta or {})
    return {"meta": meta, "vacancies": entries}
