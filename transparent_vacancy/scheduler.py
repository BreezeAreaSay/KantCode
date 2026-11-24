"""Расписание: cron-выражение из пяти полей и цикл, который по нему запускает задачу."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("минуты", "часы", "день месяца", "месяц", "день недели")


class ScheduleError(ValueError):
    """Выражение расписания не разобрано."""


def _parse_field(text: str, low: int, high: int, name: str) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        try:
            step = int(step_text) if step_text else 1
            if base == "*":
                start, end = low, high
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = int(base)
                end = high if step_text else start
        except ValueError:
            raise ScheduleError(f"расписание, поле «{name}»: не разобрано «{part}»") from None
        if step < 1 or start < low or end > high or start > end:
            raise ScheduleError(f"расписание, поле «{name}»: «{part}» вне диапазона {low}–{high}")
        values.update(range(start, end + 1, step))
    return values


class Cron:
    """``минуты часы день-месяца месяц день-недели``: ``*``, списки, диапазоны и шаг ``*/N``.

    День недели: 0 и 7 это воскресенье. Время берётся местное (переменная ``TZ``).
    """

    def __init__(self, expression: str):
        parts = expression.split()
        if len(parts) != 5:
            raise ScheduleError("расписание: нужно пять полей cron, например «0 6 * * *»")
        self.expression = expression
        fields = [_parse_field(part, low, high, name) for part, (low, high), name in zip(parts, _RANGES, _NAMES)]
        self.minutes, self.hours, self.days, self.months, weekdays = fields
        self.weekdays = {value % 7 for value in weekdays}
        self.any_day = parts[2] == "*"
        self.any_weekday = parts[4] == "*"

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours or moment.month not in self.months:
            return False
        day_ok = moment.day in self.days
        weekday_ok = (moment.isoweekday() % 7) in self.weekdays
        if self.any_day or self.any_weekday:
            return day_ok and weekday_ok
        return day_ok or weekday_ok  # как в cron: заданы оба поля — подходит любое

    def next_after(self, moment: datetime) -> datetime:
        candidate = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60 * 5):
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ScheduleError(f"расписание «{self.expression}» никогда не срабатывает")


def run_forever(
    cron: Cron,
    job: Callable[[], object],
    log: Callable[[str], None],
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
    limit: int | None = None,
) -> None:
    """Ждёт очередного срока и запускает задачу. Сбой задачи цикл не останавливает."""
    done = 0
    while limit is None or done < limit:
        due = cron.next_after(now())
        log(f"следующий запуск: {due:%Y-%m-%d %H:%M}")
        while True:
            remaining = (due - now()).total_seconds()
            if remaining <= 0:
                break
            sleep(min(remaining, 30))
        try:
            job()
        except Exception as error:  # расписание важнее одного неудачного запуска
            log(f"запуск завершился ошибкой: {error}")
        done += 1
