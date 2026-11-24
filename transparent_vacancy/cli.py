"""Командная строка: рабочий режим, проверочный режим и запуск по расписанию.

Обработка начинается здесь: ``python -m transparent_vacancy <команда>``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .analyzer import analyze_vacancies, build_client, build_report
from .config import ConfigError, Settings
from .html_report import render_html
from .letters import build_letters
from .scheduler import Cron, ScheduleError, run_forever
from .source import SourceError, fetch_vacancies
from .store import CACHE_NAME, REPORT_NAME, STATUS_NAME, Cache, read_json, write_json
from .vacancy import extract_vacancies

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_SOURCE = 2
EXIT_AI = 3


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", file=sys.stderr, flush=True)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _summary(report: dict, path: Path) -> None:
    meta = report["meta"]
    ai = meta["ai"]
    log(
        f"отчёт сохранён: {path} — вакансий {meta['vacancies']}, "
        f"с замечаниями {meta['vacancies_with_findings']}, замечаний {meta['findings_total']}"
    )
    used = ", ".join(f"{name}: {count}" for name, count in ai["models_used"].items())
    log(
        f"ИИ: {ai['status']}, модель {ai['model'] or '—'}, разобрано {ai['vacancies_reviewed']}, "
        f"из кэша {ai['vacancies_from_cache']}, сбоев {ai['vacancies_failed']}, замечаний ИИ {ai['findings']}"
    )
    if len(ai["models_used"]) > 1 or (ai["models_used"] and ai["model"] not in ai["models_used"]):
        log(f"ИИ: вакансий по моделям — {used}")
    if ai["error"]:
        log(f"ИИ, последняя ошибка: {ai['error']}")


def _ai_missing(report: dict, require_ai: bool) -> bool:
    return require_ai and report["meta"]["ai"]["status"] != "ok"


def run_once(settings: Settings, require_ai: bool = False) -> int:
    """Рабочий режим: портал → анализ → ``report.json``. При сбое источника отчёт не трогается."""
    output = settings.output_dir
    report_path = output / REPORT_NAME
    status_path = output / STATUS_NAME
    started = _now()
    previous = read_json(status_path, {}) or {}
    log(f"источник: {settings.source_url}, вакансий не больше {settings.max_vacancies}")
    try:
        fetched = fetch_vacancies(
            settings.source_url,
            settings.max_vacancies,
            settings.page_size,
            settings.http_timeout,
            settings.http_retries,
        )
        if not fetched.vacancies:
            # На неверный адрес или фильтр портал отвечает «успешно, 0 записей».
            # Пустой отчёт затёр бы прошлый, поэтому это сбой источника, а не результат.
            raise SourceError(f"{settings.source_url}: источник не вернул ни одной вакансии")
    except SourceError as error:
        kept = report_path.is_file()
        write_json(
            status_path,
            {
                "status": "error",
                "started_at": started,
                "finished_at": _now(),
                "error": str(error),
                "report": str(report_path) if kept else None,
                "last_success_at": previous.get("last_success_at"),
            },
        )
        log(f"ОШИБКА источника: {error}")
        if kept:
            log(f"последний успешный отчёт сохранён без изменений: {report_path} (от {previous.get('last_success_at')})")
        else:
            log("успешного отчёта пока нет")
        return EXIT_SOURCE

    log(f"получено вакансий: {len(fetched.vacancies)}, страниц: {fetched.pages}, всего в источнике: {fetched.total}")
    cache = Cache(output / CACHE_NAME)
    entries, ai, duplicates = analyze_vacancies(fetched.vacancies, settings, cache, log=log)
    cache.save()
    report = build_report(
        entries,
        ai,
        "api",
        settings.source_url,
        len(fetched.vacancies),
        duplicates,
        {"pages_fetched": fetched.pages, "source_total": fetched.total},
    )
    if _ai_missing(report, require_ai):
        log(f"ОШИБКА: требовался ИИ (--require-ai), но он отработал не полностью: {ai.error or ai.status}")
        write_json(
            status_path,
            {
                "status": "error",
                "started_at": started,
                "finished_at": _now(),
                "error": f"ИИ: {ai.error or ai.status}",
                "report": str(report_path) if report_path.is_file() else None,
                "last_success_at": previous.get("last_success_at"),
            },
        )
        return EXIT_AI
    write_json(report_path, report)
    finished = _now()
    write_json(
        status_path,
        {
            "status": "ok",
            "started_at": started,
            "finished_at": finished,
            "error": None,
            "report": str(report_path),
            "last_success_at": finished,
            "vacancies": report["meta"]["vacancies"],
            "findings_total": report["meta"]["findings_total"],
            "ai_status": report["meta"]["ai"]["status"],
        },
    )
    _summary(report, report_path)
    return EXIT_OK


def check_files(settings: Settings, files: list[str], out: Path, require_ai: bool, use_cache: bool) -> int:
    """Проверочный режим: локальный JSON в формате API → отчёт по всем вакансиям, без портала."""
    raw: list[dict] = []
    for name in files:
        path = Path(name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except OSError as error:
            log(f"ОШИБКА: файл не прочитан: {path}: {error.strerror or error}")
            return EXIT_USAGE
        except ValueError as error:
            log(f"ОШИБКА: {path} не является JSON: {error}")
            return EXIT_USAGE
        found = extract_vacancies(payload)
        log(f"{path}: вакансий {len(found)}")
        raw.extend(found)
    cache = Cache(settings.output_dir / CACHE_NAME if use_cache else None)
    entries, ai, duplicates = analyze_vacancies(raw, settings, cache, log=log)
    cache.save()
    report = build_report(entries, ai, "file", ", ".join(files), len(raw), duplicates)
    write_json(out, report)
    _summary(report, out)
    if _ai_missing(report, require_ai):
        log("ОШИБКА: требовался ИИ (--require-ai), но он отработал не полностью; отчёт сохранён с тем, что есть")
        return EXIT_AI
    return EXIT_OK


def write_letters(report_path: Path, out_dir: Path, only_id: str | None) -> int:
    """Дополнительный сценарий: черновики запросов работодателям по готовому отчёту."""
    report = read_json(report_path)
    if not isinstance(report, dict) or not isinstance(report.get("vacancies"), list):
        log(f"ОШИБКА: {report_path} не является отчётом. Сначала выполните run или check.")
        return EXIT_USAGE
    if only_id:
        report = {"vacancies": [entry for entry in report["vacancies"] if entry.get("id") == only_id]}
        if not report["vacancies"]:
            log(f"ОШИБКА: вакансии с ID {only_id} в отчёте нет")
            return EXIT_USAGE
    letters = build_letters(report)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in letters.items():
        (out_dir / name).write_text(text, encoding="utf-8")
    log(f"писем: {len(letters)} из {len(report['vacancies'])} вакансий, каталог {out_dir}")
    return EXIT_OK


def check_models(settings: Settings) -> int:
    """Проверяет коротким запросом, какие из настроенных моделей отвечают прямо сейчас."""
    results = build_client(settings).probe()
    for label, state in results:
        log(f"{label}: {'отвечает' if state == 'ok' else 'не отвечает — ' + state}")
    working = [label for label, state in results if state == "ok"]
    if not working:
        log("ОШИБКА: ни одна модель не отвечает")
        return EXIT_AI
    log(f"разбор начнётся с модели {working[0]}")
    return EXIT_OK


def write_html(report_path: Path, out: Path) -> int:
    """Отчёт одной HTML-страницей с фильтрами: открывается в браузере без сервера."""
    report = read_json(report_path)
    if not isinstance(report, dict) or not isinstance(report.get("vacancies"), list):
        log(f"ОШИБКА: {report_path} не является отчётом. Сначала выполните run или check.")
        return EXIT_USAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(report), encoding="utf-8")
    log(f"страница отчёта: {out}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transparent_vacancy",
        description="Прозрачная вакансия: отчёт о том, насколько понятно описаны условия работы.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--env-file", default=".env", help="файл с настройками TV_* (по умолчанию .env)")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--no-ai", action="store_true", help="только правила, без модели")
        command.add_argument("--require-ai", action="store_true", help="завершиться ошибкой, если модель недоступна")
        command.add_argument("--ai-limit", type=int, metavar="N", help="отдать модели не больше N вакансий")

    run = commands.add_parser("run", help="рабочий режим: загрузить вакансии с портала и сохранить отчёт")
    run.add_argument("--source-url", help="адрес API (TV_SOURCE_URL)")
    run.add_argument("--max", type=int, metavar="N", help="максимум вакансий (TV_MAX_VACANCIES)")
    run.add_argument("--out-dir", help="каталог отчёта (TV_OUTPUT_DIR)")
    common(run)

    check = commands.add_parser("check", help="проверочный режим: отчёт по локальному JSON в формате API")
    check.add_argument("files", nargs="+", help="файлы JSON в формате ответов API")
    check.add_argument("--out", help="куда сохранить отчёт (по умолчанию <каталог отчёта>/check-report.json)")
    check.add_argument("--no-cache", action="store_true", help="не брать прошлые разборы из кэша")
    common(check)

    schedule = commands.add_parser("schedule", help="запускать рабочий режим по расписанию TV_SCHEDULE")
    schedule.add_argument("--cron", help="cron-выражение (TV_SCHEDULE)")
    schedule.add_argument("--run-on-start", action="store_true", help="сначала выполнить один запуск сразу")
    common(schedule)

    letters = commands.add_parser("letters", help="доп. сценарий: черновики запросов работодателям по отчёту")
    letters.add_argument("--report", help="отчёт (по умолчанию <каталог отчёта>/report.json)")
    letters.add_argument("--out-dir", help="куда сохранить письма (по умолчанию <каталог отчёта>/letters)")
    letters.add_argument("--id", help="только вакансия с этим ID")

    commands.add_parser("models", help="проверить, какие из настроенных моделей отвечают сейчас")

    html = commands.add_parser("html", help="отчёт одной HTML-страницей с фильтрами")
    html.add_argument("--report", help="отчёт (по умолчанию <каталог отчёта>/report.json)")
    html.add_argument("--out", help="куда сохранить страницу (по умолчанию рядом с отчётом, .html)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.load(args.env_file)
        if args.command == "letters":
            report_path = Path(args.report) if args.report else settings.output_dir / REPORT_NAME
            out_dir = Path(args.out_dir) if args.out_dir else settings.output_dir / "letters"
            return write_letters(report_path, out_dir, args.id)
        if args.command == "html":
            report_path = Path(args.report) if args.report else settings.output_dir / REPORT_NAME
            return write_html(report_path, Path(args.out) if args.out else report_path.with_suffix(".html"))
        if args.command == "models":
            return check_models(settings)
        if args.no_ai:
            settings.ai_enabled = False
        if args.ai_limit is not None:
            settings.ai_max_vacancies = max(args.ai_limit, 0)
        if args.command == "check":
            out = Path(args.out) if args.out else settings.output_dir / "check-report.json"
            return check_files(settings, args.files, out, args.require_ai, not args.no_cache)
        if args.command == "run":
            if args.source_url:
                settings.source_url = args.source_url
            if args.max:
                settings.max_vacancies = args.max
            if args.out_dir:
                settings.output_dir = Path(args.out_dir)
            return run_once(settings, args.require_ai)
        cron = Cron(args.cron or settings.schedule)
        log(f"расписание: {cron.expression}")
        if args.run_on_start:
            run_once(settings, args.require_ai)
        run_forever(cron, lambda: run_once(settings, args.require_ai), log)
        return EXIT_OK
    except (ConfigError, ScheduleError) as error:
        log(f"ОШИБКА настройки: {error}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        log("остановлено")
        return 130
