import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import FakeModel, FakePortal, api_response, vacancy
from transparent_vacancy import __version__, cli

VAGUE_DUTY = "Проведение инъекций и забор крови. Возможны командировки."


def model_reply(fields: dict) -> list[dict]:
    """Модель-заглушка: одно настоящее замечание и одно выдуманное."""
    if "Возможны командировки" not in fields.get("duty", ""):
        return []
    return [
        {
            "topic": "duties", "type": "ambiguity", "field": "duty", "quote": "Возможны командировки",
            "problem": "Из формулировки нельзя понять, как часто и куда придётся ездить.",
            "recommendation": "Уточните у работодателя, как часто бывают командировки.",
        },
        {
            "topic": "schedule", "type": "ambiguity", "field": "duty", "quote": "удобный график",
            "problem": "Формулировка о графике расплывчатая и ничего не сообщает.",
            "recommendation": "Уточните график.",
        },
    ]


class CliCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.model = FakeModel(model_reply)
        self.addCleanup(self.model.close)
        self.portal = FakePortal(
            [vacancy(id=f"id-{number}", **({"duty": VAGUE_DUTY} if number % 2 else {})) for number in range(6)]
        )
        self.addCleanup(self.portal.close)
        saved = {key: value for key, value in os.environ.items() if key.startswith("TV_")}
        for key in saved:
            del os.environ[key]
        self.addCleanup(lambda: os.environ.update(saved))
        self.addCleanup(lambda: [os.environ.pop(key) for key in list(os.environ) if key.startswith("TV_")])
        os.environ.update(
            TV_SOURCE_URL=self.portal.url + "/api/v1/vacancies/region/39",
            TV_OUTPUT_DIR=str(self.dir / "reports"),
            TV_PAGE_SIZE="3",
            TV_MAX_VACANCIES="6",
            TV_HTTP_RETRIES="1",
            TV_AI_BASE_URL=self.model.url + "/v1",
            TV_AI_MODEL="stub",
            TV_AI_WORKERS="1",
            TV_AI_RETRIES="1",
        )

    def call(self, *argv: str) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.main(["--env-file", str(self.dir / "absent.env"), *argv])
        return code, stderr.getvalue()

    def report(self, name: str = "report.json") -> dict:
        return json.loads((self.dir / "reports" / name).read_text(encoding="utf-8"))

    def status(self) -> dict:
        return json.loads((self.dir / "reports" / "last_run.json").read_text(encoding="utf-8"))


class RunTest(CliCase):
    def test_run_reads_two_pages_and_ai_changes_the_report(self):
        code, _ = self.call("run")
        self.assertEqual(code, 0)
        report = self.report()
        meta = report["meta"]
        self.assertEqual((meta["mode"], meta["vacancies"], meta["pages_fetched"], meta["source_total"]), ("api", 6, 2, 6))
        self.assertEqual(meta["ai"]["status"], "ok")
        self.assertEqual((meta["ai"]["vacancies_reviewed"], meta["ai"]["findings"]), (6, 3))
        self.assertEqual([item["id"] for item in report["vacancies"]], [f"id-{number}" for number in range(6)])
        with_trips = report["vacancies"][1]
        self.assertTrue(with_trips["ai_reviewed"])
        self.assertEqual(
            [(item["topic"], item["detected_by"], item["quote"]) for item in with_trips["findings"]],
            [("duties", "ai", "Возможны командировки")],
        )
        self.assertEqual(report["vacancies"][0]["findings"], [])  # замечаний нет — пустой массив
        self.assertEqual(self.status()["status"], "ok")

    def test_rerun_on_same_data_keeps_one_result_per_id_and_reuses_analysis(self):
        self.call("run")
        first = self.report()
        calls = self.model.calls
        code, _ = self.call("run")
        second = self.report()
        self.assertEqual(code, 0)
        self.assertEqual(self.model.calls, calls)  # модель второй раз не спрашивали
        self.assertEqual(second["vacancies"], first["vacancies"])
        self.assertEqual(second["meta"]["ai"]["vacancies_from_cache"], 6)
        ids = [item["id"] for item in second["vacancies"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_changed_vacancy_is_analysed_again(self):
        self.call("run")
        calls = self.model.calls
        self.portal.vacancies[0]["duty"] = VAGUE_DUTY
        self.call("run")
        self.assertEqual(self.model.calls, calls + 1)
        self.assertEqual(len(self.report()["vacancies"][0]["findings"]), 1)

    def test_portal_failure_keeps_last_successful_report(self):
        self.call("run")
        before = (self.dir / "reports" / "report.json").read_bytes()
        success_time = self.status()["last_success_at"]
        self.portal.fail_with = 500
        code, output = self.call("run")
        self.assertEqual(code, cli.EXIT_SOURCE)
        self.assertEqual((self.dir / "reports" / "report.json").read_bytes(), before)
        status = self.status()
        self.assertEqual((status["status"], status["last_success_at"]), ("error", success_time))
        self.assertIn("HTTP 500", status["error"])
        self.assertIn("ОШИБКА источника", output)
        self.assertIn("последний успешный отчёт сохранён", output)

    def test_empty_answer_from_portal_does_not_erase_the_report(self):
        self.call("run")
        before = (self.dir / "reports" / "report.json").read_bytes()
        self.portal.vacancies = []  # портал отвечает «успешно, 0 записей», как на неверный адрес
        code, output = self.call("run")
        self.assertEqual(code, cli.EXIT_SOURCE)
        self.assertEqual((self.dir / "reports" / "report.json").read_bytes(), before)
        self.assertIn("не вернул ни одной вакансии", self.status()["error"])

    def test_portal_failure_without_earlier_report(self):
        self.portal.fail_with = 500
        code, output = self.call("run")
        self.assertEqual(code, cli.EXIT_SOURCE)
        self.assertFalse((self.dir / "reports" / "report.json").exists())
        self.assertIn("успешного отчёта пока нет", output)

    def test_model_down_gives_rule_report_and_says_so(self):
        self.model.fail = True
        code, output = self.call("run")
        self.assertEqual(code, 0)
        ai = self.report()["meta"]["ai"]
        self.assertEqual((ai["status"], ai["vacancies_reviewed"]), ("unavailable", 0))
        self.assertEqual(ai["vacancies_failed"] + ai["vacancies_skipped"], 6)
        self.assertIn("HTTP 503", ai["error"])
        self.assertFalse(any(item["ai_reviewed"] for item in self.report()["vacancies"]))
        self.assertLessEqual(self.model.calls, 3)  # после трёх сбоев подряд модель больше не дёргают

    def test_require_ai_fails_when_model_is_down_and_keeps_report(self):
        self.call("run")
        before = (self.dir / "reports" / "report.json").read_bytes()
        self.model.fail = True
        self.portal.vacancies[0]["duty"] = VAGUE_DUTY
        code, _ = self.call("run", "--require-ai")
        self.assertEqual(code, cli.EXIT_AI)
        self.assertEqual((self.dir / "reports" / "report.json").read_bytes(), before)

    def test_no_ai_flag(self):
        code, _ = self.call("run", "--no-ai")
        self.assertEqual(code, 0)
        self.assertEqual(self.report()["meta"]["ai"]["status"], "disabled")
        self.assertEqual(self.model.calls, 0)


    def test_command_line_keys_override_settings(self):
        other = self.dir / "other"
        code, output = self.call(
            "run", "--source-url", self.portal.url + "/api/v1/vacancies/region/39?text=x", "--max", "4",
            "--out-dir", str(other), "--ai-limit", "1",
        )
        self.assertEqual(code, 0)
        self.assertIn("вакансий не больше 4", output)
        report = json.loads((other / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["meta"]["vacancies"], 4)
        self.assertIn("text=x", report["meta"]["source"])
        ai = report["meta"]["ai"]
        self.assertEqual((ai["status"], ai["vacancies_reviewed"], ai["vacancies_skipped"]), ("partial", 1, 3))
        self.assertEqual([item["ai_reviewed"] for item in report["vacancies"]], [True, False, False, False])
        self.assertFalse((self.dir / "reports" / "report.json").exists())

    def test_several_workers_keep_the_order_and_report_progress(self):
        self.portal.vacancies[:] = [vacancy(id=f"id-{number}", duty=VAGUE_DUTY) for number in range(10)]
        os.environ.update(TV_AI_WORKERS="3", TV_MAX_VACANCIES="10", TV_PAGE_SIZE="5")
        code, output = self.call("run")
        self.assertEqual(code, 0)
        self.assertIn("ИИ: разобрано вакансий: 10", output)
        report = self.report()
        self.assertEqual([item["id"] for item in report["vacancies"]], [f"id-{number}" for number in range(10)])
        self.assertEqual((report["meta"]["ai"]["status"], report["meta"]["ai"]["findings"]), ("ok", 10))


class FallbackModelsTest(CliCase):
    def test_busy_model_is_found_before_the_run_and_the_spare_one_does_the_work(self):
        os.environ["TV_AI_FALLBACK_MODELS"] = "backup"
        self.model.fail_models = {"stub"}
        code, output = self.call("run", "--require-ai")
        self.assertEqual(code, 0)
        self.assertIn("проверка модели stub: не отвечает — HTTP 503", output)
        self.assertIn("проверка модели backup: отвечает", output)
        self.assertIn("вакансий по моделям — backup: 6", output)
        report = self.report()
        ai = report["meta"]["ai"]
        self.assertEqual((ai["status"], ai["model"], ai["fallback_models"]), ("ok", "stub", ["backup"]))
        self.assertEqual(ai["models_used"], {"backup": 6})
        self.assertEqual(ai["models_checked"]["backup"], "ok")
        self.assertIn("HTTP 503", ai["models_checked"]["stub"])
        self.assertEqual({item["ai_model"] for item in report["vacancies"]}, {"backup"})
        self.assertEqual(set(self.model.asked), {"backup"})  # занятую модель по вакансиям не спрашивали
        self.assertEqual(self.model.probes, 2)

    def test_model_that_gets_busy_in_the_middle_of_the_run(self):
        os.environ["TV_AI_FALLBACK_MODELS"] = "backup"
        answered = []

        def reply(fields):
            answered.append(1)
            if len(answered) == 2:
                self.model.fail_models = {"stub"}  # после второй вакансии основная модель упирается в лимит
            return model_reply(fields)

        self.model.reply = reply
        code, output = self.call("run", "--require-ai")
        self.assertEqual(code, 0)
        self.assertIn("модель stub не ответила (HTTP 503), запрос уходит модели backup", output)
        report = self.report()
        self.assertEqual(report["meta"]["ai"]["models_used"], {"stub": 2, "backup": 4})
        self.assertEqual([item["ai_model"] for item in report["vacancies"]], ["stub", "stub"] + ["backup"] * 4)
        self.assertEqual(self.model.asked.count("stub"), 3)  # третий запрос показал лимит, дальше её не трогали

    def test_no_model_answers(self):
        os.environ["TV_AI_FALLBACK_MODELS"] = "backup"
        self.model.fail = True
        code, output = self.call("run")
        self.assertEqual(code, 0)
        self.assertIn("ни одна модель не ответила", output)
        ai = self.report()["meta"]["ai"]
        self.assertEqual((ai["status"], ai["vacancies_skipped"], ai["models_used"]), ("unavailable", 6, {}))
        self.assertEqual(self.model.calls, 0)  # хватило проверки: по вакансиям запросы не уходили
        self.assertEqual({item["ai_model"] for item in self.report()["vacancies"]}, {None})

    def test_models_are_not_checked_when_everything_comes_from_the_cache(self):
        self.call("run")
        probes = self.model.probes
        self.assertEqual(probes, 1)
        self.call("run")
        self.assertEqual(self.model.probes, probes)
        ai = self.report()["meta"]["ai"]
        self.assertEqual((ai["models_used"], ai["models_checked"]), ({"stub": 6}, {}))
        self.assertEqual({item["ai_model"] for item in self.report()["vacancies"]}, {"stub"})

    def test_models_are_not_checked_without_ai(self):
        self.call("run", "--no-ai")
        self.assertEqual((self.model.probes, self.model.calls), (0, 0))

    def test_models_command(self):
        os.environ["TV_AI_FALLBACK_MODELS"] = "backup, third"
        self.model.fail_models = {"stub", "third"}
        code, output = self.call("models")
        self.assertEqual(code, 0)
        self.assertIn("stub: не отвечает — HTTP 503", output)
        self.assertIn("backup: отвечает", output)
        self.assertIn("разбор начнётся с модели backup", output)
        self.assertEqual((self.model.probes, self.model.calls), (3, 0))
        self.model.fail = True
        code, output = self.call("models")
        self.assertEqual(code, cli.EXIT_AI)
        self.assertIn("ни одна модель не отвечает", output)


class ScheduleCommandTest(CliCase):
    def test_schedule_runs_the_same_job_as_the_manual_run(self):
        with mock.patch.object(cli, "run_forever") as loop:
            code, output = self.call("schedule", "--cron", "*/5 * * * *", "--run-on-start", "--no-ai")
        self.assertEqual(code, 0)
        self.assertIn("расписание: */5 * * * *", output)
        self.assertEqual(self.status()["status"], "ok")  # запуск сразу, до ожидания срока
        first = self.status()["finished_at"]
        cron, job, _ = loop.call_args.args
        self.assertEqual(cron.expression, "*/5 * * * *")
        self.portal.vacancies[0]["employment"] = None
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(job(), 0)  # то, что планировщик вызывает по сроку
        self.assertEqual(self.report()["vacancies"][0]["findings"][0]["topic"], "employment")
        self.assertGreaterEqual(self.status()["finished_at"], first)

    def test_schedule_is_taken_from_settings_and_waits_for_the_due_time(self):
        os.environ["TV_SCHEDULE"] = "15 3 * * *"
        with mock.patch.object(cli, "run_forever") as loop:
            code, _ = self.call("schedule")
        self.assertEqual(code, 0)
        self.assertEqual(loop.call_args.args[0].expression, "15 3 * * *")
        self.assertEqual(self.portal.requests, [])  # без --run-on-start до срока к порталу не ходим

    def test_bad_schedule(self):
        code, output = self.call("schedule", "--cron", "61 * * * *")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("ОШИБКА настройки", output)

    def test_interrupt_stops_quietly(self):
        with mock.patch.object(cli, "run_forever", side_effect=KeyboardInterrupt):
            code, output = self.call("schedule")
        self.assertEqual(code, 130)
        self.assertIn("остановлено", output)


class EntryPointTest(unittest.TestCase):
    def run_module(self, *argv: str) -> subprocess.CompletedProcess:
        root = Path(__file__).resolve().parent.parent
        return subprocess.run(
            [sys.executable, "-m", "transparent_vacancy", *argv], cwd=root, capture_output=True, text=True, timeout=60
        )

    def test_module_is_runnable(self):
        done = self.run_module("--version")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, __version__))

    def test_command_is_required(self):
        done = self.run_module()
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage", done.stderr)

    def test_exit_code_of_the_command_becomes_the_exit_code_of_the_process(self):
        done = self.run_module("--env-file", "absent.env", "check", "absent.json", "--no-ai")
        self.assertEqual(done.returncode, cli.EXIT_USAGE)

class CheckTest(CliCase):
    def write(self, name: str, payload) -> str:
        path = self.dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_check_reads_local_json_without_the_portal(self):
        source = self.write(
            "sample.json",
            api_response([vacancy(id="a", duty=VAGUE_DUTY), vacancy(id="b", employment=None), vacancy(id="b", employment=None)]),
        )
        out = self.dir / "out.json"
        code, _ = self.call("check", source, "--out", str(out))
        self.assertEqual(code, 0)
        self.assertEqual(self.portal.requests, [])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((report["meta"]["mode"], report["meta"]["vacancies_received"], report["meta"]["duplicates_merged"]), ("file", 3, 1))
        self.assertEqual([item["id"] for item in report["vacancies"]], ["a", "b"])
        gap = report["vacancies"][1]["findings"][0]
        self.assertEqual(
            {key: gap[key] for key in ("topic", "type", "field", "quote", "detected_by")},
            {"topic": "employment", "type": "gap", "field": "employment", "quote": "", "detected_by": "rule"},
        )
        self.assertEqual(report["vacancies"][0]["findings"][0]["detected_by"], "ai")

    def test_check_is_not_limited_by_max_vacancies(self):
        os.environ["TV_MAX_VACANCIES"] = "2"
        source = self.write("many.json", [vacancy(id=f"v{number}") for number in range(5)])
        self.call("check", source)
        self.assertEqual(self.report("check-report.json")["meta"]["vacancies"], 5)

    def test_several_files_make_one_report(self):
        first = self.write("page1.json", api_response([vacancy(id="a"), vacancy(id="b")]))
        second = self.write("page2.json", api_response([vacancy(id="b"), vacancy(id="c")]))
        code, output = self.call("check", first, second, "--no-ai", "--no-cache")
        self.assertEqual(code, 0)
        self.assertIn("page2.json: вакансий 2", output)
        meta = self.report("check-report.json")["meta"]
        self.assertEqual((meta["vacancies_received"], meta["duplicates_merged"], meta["vacancies"]), (4, 1, 3))
        self.assertEqual(meta["source"], f"{first}, {second}")
        self.assertFalse((self.dir / "reports" / "cache.json").exists())

    def test_require_ai_in_check_mode_saves_the_report_and_fails(self):
        self.model.fail = True
        source = self.write("sample.json", api_response([vacancy(id="a", employment=None)]))
        out = self.dir / "out.json"
        code, output = self.call("check", source, "--out", str(out), "--require-ai")
        self.assertEqual(code, cli.EXIT_AI)
        self.assertIn("требовался ИИ", output)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["meta"]["ai"]["status"], "unavailable")
        self.assertEqual([item["topic"] for item in report["vacancies"][0]["findings"]], ["employment"])

    def test_cache_makes_the_second_check_free(self):
        source = self.write("sample.json", api_response([vacancy(id="a", duty=VAGUE_DUTY)]))
        self.call("check", source)
        calls = self.model.calls
        code, _ = self.call("check", source, "--require-ai")
        self.assertEqual(code, 0)
        self.assertEqual(self.model.calls, calls)
        ai = self.report("check-report.json")["meta"]["ai"]
        self.assertEqual((ai["status"], ai["vacancies_from_cache"], ai["findings"]), ("ok", 1, 1))
        self.call("check", source, "--no-cache")
        self.assertEqual(self.model.calls, calls + 1)

    def test_bad_file(self):
        code, output = self.call("check", str(self.dir / "missing.json"))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("файл не прочитан", output)
        broken = self.dir / "broken.json"
        broken.write_text("{не json", encoding="utf-8")
        code, output = self.call("check", str(broken))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("не является JSON", output)

    def test_bad_settings(self):
        os.environ["TV_MAX_VACANCIES"] = "много"
        code, output = self.call("run")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("TV_MAX_VACANCIES", output)


if __name__ == "__main__":
    unittest.main()
