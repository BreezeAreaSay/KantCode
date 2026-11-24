import unittest
from datetime import datetime, timedelta

from transparent_vacancy.scheduler import Cron, ScheduleError, run_forever


class CronTest(unittest.TestCase):
    def test_daily(self):
        cron = Cron("0 6 * * *")
        self.assertEqual(cron.next_after(datetime(2026, 11, 24, 5, 59)), datetime(2026, 11, 24, 6, 0))
        self.assertEqual(cron.next_after(datetime(2026, 11, 24, 6, 0)), datetime(2026, 11, 25, 6, 0))

    def test_step_list_and_range(self):
        self.assertEqual(Cron("*/15 * * * *").next_after(datetime(2026, 11, 24, 10, 16)), datetime(2026, 11, 24, 10, 30))
        self.assertEqual(Cron("30 8,20 * * *").next_after(datetime(2026, 11, 24, 9, 0)), datetime(2026, 11, 24, 20, 30))
        # 24 ноября 2026 года — вторник; пн-пт в 9:00
        self.assertEqual(Cron("0 9 * * 1-5").next_after(datetime(2026, 11, 27, 9, 0)), datetime(2026, 11, 30, 9, 0))

    def test_sunday_is_zero_and_seven(self):
        sunday = datetime(2026, 11, 29, 0, 0)
        self.assertEqual(Cron("0 0 * * 0").next_after(datetime(2026, 11, 24)), sunday)
        self.assertEqual(Cron("0 0 * * 7").next_after(datetime(2026, 11, 24)), sunday)

    def test_day_of_month_and_weekday_together_mean_either(self):
        cron = Cron("0 6 13 * 5")  # 13-е число или пятница, как в обычном cron
        self.assertEqual(cron.next_after(datetime(2026, 11, 24, 12, 0)), datetime(2026, 11, 27, 6, 0))  # пятница
        self.assertEqual(cron.next_after(datetime(2026, 12, 11, 6, 0)), datetime(2026, 12, 13, 6, 0))  # воскресенье, 13-е

    def test_bad_expressions(self):
        for expression in ("", "0 6 * *", "61 * * * *", "a * * * *", "*/0 * * * *", "5-1 * * * *"):
            with self.subTest(expression=expression):
                with self.assertRaises(ScheduleError):
                    Cron(expression)

    def test_impossible_date(self):
        with self.assertRaises(ScheduleError):
            Cron("0 0 31 2 *").next_after(datetime(2026, 1, 1))


class LoopTest(unittest.TestCase):
    def test_job_runs_at_due_time_and_survives_failures(self):
        clock = [datetime(2026, 11, 24, 5, 58, 30)]
        runs: list[datetime] = []
        messages: list[str] = []

        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)

        def job():
            runs.append(clock[0])
            if len(runs) == 1:
                raise RuntimeError("портал лежит")

        run_forever(Cron("0 6 * * *"), job, messages.append, now=lambda: clock[0], sleep=sleep, limit=2)
        self.assertEqual(runs, [datetime(2026, 11, 24, 6, 0), datetime(2026, 11, 25, 6, 0)])
        self.assertTrue(any("портал лежит" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
