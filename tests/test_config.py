import os
import tempfile
import unittest
from pathlib import Path

from transparent_vacancy.config import API_PAGE_LIMIT, ConfigError, Settings, load_env_file


class EnvCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        saved = {key: value for key, value in os.environ.items() if key.startswith("TV_")}
        for key in saved:
            del os.environ[key]
        self.addCleanup(lambda: os.environ.update(saved))
        self.addCleanup(lambda: [os.environ.pop(key) for key in list(os.environ) if key.startswith("TV_")])

    def env_file(self, text: str) -> Path:
        path = self.dir / ".env"
        path.write_text(text, encoding="utf-8")
        return path


class EnvFileTest(EnvCase):
    def test_comments_blank_lines_and_quotes(self):
        path = self.env_file(
            "# настройки\n"
            "\n"
            "TV_AI_MODEL = 'gemma3:4b'\n"
            'TV_SCHEDULE="30 7 * * 1-5"\n'
            "строка без знака равенства\n"
            "TV_AI_API_KEY=abc=def\n"
        )
        self.assertEqual(
            load_env_file(path),
            {"TV_AI_MODEL": "gemma3:4b", "TV_SCHEDULE": "30 7 * * 1-5", "TV_AI_API_KEY": "abc=def"},
        )

    def test_missing_file_gives_defaults(self):
        self.assertEqual(load_env_file(self.dir / "absent.env"), {})
        settings = Settings.load(self.dir / "absent.env")
        self.assertEqual((settings.max_vacancies, settings.page_size, settings.schedule), (100, 50, "0 6 * * *"))
        self.assertTrue(settings.ai_enabled and settings.ai_strict)
        self.assertEqual(settings.ai_api_key, "")

    def test_file_is_read_and_environment_wins(self):
        path = self.env_file("TV_MAX_VACANCIES=30\nTV_AI_MODEL=из-файла\nTV_OUTPUT_DIR=out\n")
        os.environ["TV_AI_MODEL"] = "из-окружения"
        settings = Settings.load(path)
        self.assertEqual((settings.max_vacancies, settings.ai_model, settings.output_dir), (30, "из-окружения", Path("out")))

    def test_empty_value_means_default(self):
        settings = Settings.load(self.env_file("TV_AI_MODEL=\nTV_PAGE_SIZE=\n"))
        self.assertEqual((settings.ai_model, settings.page_size), ("qwen2.5:7b", 50))


class ValuesTest(EnvCase):
    def load(self, **values) -> Settings:
        os.environ.update(values)
        return Settings.load(None)

    def test_flags(self):
        for raw, expected in (("false", False), ("0", False), ("нет", False), ("True", True), ("1", True), ("да", True)):
            with self.subTest(raw=raw):
                self.assertEqual(self.load(TV_AI_ENABLED=raw, TV_AI_STRICT=raw).ai_enabled, expected)
                self.assertEqual(self.load(TV_AI_ENABLED=raw, TV_AI_STRICT=raw).ai_strict, expected)

    def test_page_size_is_capped_by_the_portal_limit(self):
        self.assertEqual(self.load(TV_PAGE_SIZE="500").page_size, API_PAGE_LIMIT)

    def test_trailing_slash_of_model_address_is_dropped(self):
        self.assertEqual(self.load(TV_AI_BASE_URL="https://openrouter.ai/api/v1/").ai_base_url, "https://openrouter.ai/api/v1")

    def test_numbers_must_be_whole_and_not_below_minimum(self):
        for name, raw in (("TV_MAX_VACANCIES", "0"), ("TV_PAGE_SIZE", "-5"), ("TV_AI_TIMEOUT", "1.5"), ("TV_AI_MAX_VACANCIES", "-1")):
            with self.subTest(name=name, raw=raw):
                with self.assertRaises(ConfigError) as caught:
                    self.load(**{name: raw})
                self.assertIn(name, str(caught.exception))
                del os.environ[name]
        self.assertEqual(self.load(TV_AI_MAX_VACANCIES="0").ai_max_vacancies, 0)

    def test_source_must_be_an_http_address(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(TV_SOURCE_URL="ftp://example.org/vacancies")
        self.assertIn("TV_SOURCE_URL", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
