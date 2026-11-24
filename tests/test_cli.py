import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeModel, FakePortal, api_response, vacancy
from transparent_vacancy import cli

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
