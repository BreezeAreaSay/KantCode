import unittest

from tests.helpers import vacancy
from transparent_vacancy import rules
from transparent_vacancy.vacancy import Vacancy


def findings(**changes):
    return rules.analyze(Vacancy(vacancy(**changes)))[0]


def kinds(**changes):
    return [(item.topic, item.kind) for item in findings(**changes)]


class CleanVacancyTest(unittest.TestCase):
    def test_concrete_vacancy_has_no_findings(self):
        self.assertEqual(kinds(), [])

    def test_empty_vacancy_reports_every_topic_once(self):
        result = [(item.topic, item.kind) for item in rules.analyze(Vacancy({"id": "x"}))[0]]
        self.assertEqual(
            result,
            [
                ("title", "gap"),
                ("duties", "gap"),
                ("requirements", "gap"),
                ("salary", "gap"),
                ("schedule", "gap"),
                ("location", "gap"),
                ("employment", "gap"),
            ],
        )

    def test_gap_has_empty_quote_and_names_the_field(self):
        (item,) = findings(employment=None)
        self.assertEqual((item.field, item.quote, item.kind), ("employment", "", "gap"))
        self.assertTrue(item.problem and item.recommendation)


class TitleTest(unittest.TestCase):
    def test_generic_title_is_ambiguous(self):
        self.assertEqual(kinds(job__name="Специалист"), [("title", "ambiguity")])

    def test_specific_title_is_fine(self):
        self.assertEqual(kinds(job__name="Специалист по охране труда"), [])


class DutiesTest(unittest.TestCase):
    def test_missing_duty_is_gap_unless_skills_listed(self):
        self.assertEqual(kinds(duty=""), [("duties", "gap")])
        self.assertEqual(kinds(duty="", skills=["Проведение инъекций"]), [])

    def test_open_ended_list(self):
        (item,) = findings(duty="Ведение документации, приём граждан и другие поручения руководителя.")
        self.assertEqual((item.topic, item.kind, item.field), ("duties", "ambiguity", "duty"))
        self.assertIn("другие поручения", item.quote)

    def test_too_short(self):
        self.assertEqual(kinds(duty="стирка белья"), [("duties", "ambiguity")])

    def test_reference_to_a_job_description_instead_of_duties(self):
        (item,) = findings(duty="Работа согласно должностной инструкции медицинской сестры.")
        self.assertEqual((item.topic, item.kind, item.field), ("duties", "ambiguity", "duty"))
        self.assertIn("согласно должностной инструкции", item.quote)
        detailed = (
            "Выполнение назначений врача, проведение инъекций и забор крови, ведение медицинской документации, "
            "подготовка кабинета к приёму и другая работа согласно должностной инструкции."
        )
        self.assertEqual(kinds(duty=detailed), [])

    def test_nothing_but_unnamed_errands(self):
        for duty in ("Выполнение различных поручений руководителя.", "Работа с документами и другие задачи по необходимости."):
            with self.subTest(duty=duty):
                (item,) = findings(duty=duty)
                self.assertEqual((item.topic, item.kind, item.quote), ("duties", "ambiguity", duty))
                self.assertIn("ни одна конкретная задача", item.problem)


class RequirementsTest(unittest.TestCase):
    def test_structured_fields_alone_are_enough(self):
        self.assertEqual(kinds(requirements="", qualification=""), [])

    def test_nothing_at_all_is_gap(self):
        self.assertEqual(kinds(requirements="", qualification="", requirement={}), [("requirements", "gap")])

    def test_experience_mentioned_in_text_closes_the_gap(self):
        self.assertEqual(
            kinds(requirement={"education": "Высшее образование — бакалавриат"}, requirements="Опыт работы от 1 года"),
            [],
        )
        self.assertEqual(
            kinds(requirement={"education": "Высшее образование — бакалавриат"}, requirements="Аккуратность"),
            [("requirements", "gap")],
        )

    def test_experience_conflict_gives_both_values(self):
        (item,) = findings(requirements="Опыт работы в аналогичной должности от 3 лет")
        self.assertEqual((item.topic, item.kind), ("requirements", "contradiction"))
        self.assertEqual(item.conflict[0], {"field": "requirement.experience", "value": "1"})
        self.assertEqual(item.conflict[1]["field"], "requirements")
        self.assertIn("от 3 лет", item.conflict[1]["value"])

    def test_same_experience_or_desired_experience_is_fine(self):
        self.assertEqual(kinds(requirements="Опыт работы от 1 года"), [])
        self.assertEqual(kinds(requirements="Желателен опыт работы от 3 лет"), [])
        self.assertEqual(
            kinds(requirements="Высшее образование без предъявления требований к стажу или среднее и стаж не менее 3 лет"),
            [],
        )

    def test_no_experience_needed_against_field(self):
        result = kinds(requirement={"education": "Общее образование", "experience": 3}, requirements="Можно без опыта")
        self.assertEqual(result, [("requirements", "contradiction")])

    def test_experience_conflict_when_years_come_before_the_word(self):
        (item,) = findings(
            requirement={"education": "Высшее", "experience": 0, "qualification": "Требуется не менее 5 лет опыта работы аналитиком."},
            requirements="Знание SQL.",
        )
        self.assertEqual((item.topic, item.kind), ("requirements", "contradiction"))
        self.assertEqual(item.conflict[0], {"field": "requirement.experience", "value": "0"})
        self.assertEqual(item.conflict[1], {"field": "requirement.qualification", "value": "не менее 5 лет опыта"})

    def test_unfilled_education_is_fine_when_requirements_are_concrete(self):
        for text in ("Внимательность, базовые навыки работы на компьютере.", "Знание SQL и электронных таблиц.", "Опыт разработки учебных материалов."):
            with self.subTest(text=text):
                self.assertEqual(kinds(requirement={"education": "Не указано", "experience": 0}, requirements=text), [])

    def test_unfilled_education_with_only_general_words_is_gap(self):
        for text in ("Ответственность, активность.", "Опыт работы приветствуется."):
            with self.subTest(text=text):
                (item,) = findings(requirement={"education": "Не указано", "experience": 0}, requirements=text)
                self.assertEqual((item.topic, item.kind, item.field, item.quote), ("requirements", "gap", "requirement.education", ""))
                self.assertIn("общими словами", item.problem)

    def test_unfilled_education_without_any_requirement_text_is_gap(self):
        (item,) = findings(requirement={"education": "Не указано", "experience": 0}, requirements="", qualification="")
        self.assertEqual((item.topic, item.kind, item.field), ("requirements", "gap", "requirement.education"))
        self.assertIn("текста требований нет", item.problem)

    def test_education_conflict(self):
        result = kinds(
            requirement={"education": "Tребования не предъявляются", "experience": 1},
            requirements="Высшее медицинское образование",
        )
        self.assertEqual(result, [("requirements", "contradiction")])


class SalaryTest(unittest.TestCase):
    def test_no_salary_anywhere_is_gap(self):
        self.assertEqual(kinds(salary="", salary_min=0, salary_max=0), [("salary", "gap")])

    def test_words_instead_of_amount(self):
        (item,) = findings(salary="", salary_min=None, salary_max=None, duty="Проведение инъекций. Зарплата по результатам собеседования.")
        self.assertEqual((item.topic, item.kind, item.field), ("salary", "ambiguity", "duty"))
        self.assertIn("по результатам собеседования", item.quote)

    def test_vague_words_do_not_matter_when_amount_is_in_fields(self):
        self.assertEqual(kinds(duty="Проведение инъекций и забор крови. Достойная заработная плата."), [])

    def test_amount_only_in_text_is_enough(self):
        self.assertEqual(
            kinds(salary="", salary_min=None, salary_max=None, duty="Проведение инъекций. Заработная плата от 45 000 рублей."),
            [],
        )

    def test_min_above_max(self):
        (item,) = findings(salary="от 70000", salary_min=70000, salary_max=50000)
        self.assertEqual((item.topic, item.kind), ("salary", "contradiction"))
        self.assertEqual([part["value"] for part in item.conflict], ["70000", "50000"])

    def test_text_amount_outside_field_range(self):
        (item,) = findings(
            salary="от 90000", salary_min=90000, salary_max=92000,
            requirements="Среднее профессиональное образование. Размер официальной заработной платы: до 67800 руб.",
        )
        self.assertEqual((item.topic, item.kind), ("salary", "contradiction"))
        self.assertEqual(item.conflict[0]["value"], "90000–92000")
        self.assertIn("67800", item.conflict[1]["value"])

    def test_bonus_amount_is_not_salary(self):
        self.assertEqual(
            kinds(duty="Проведение инъекций и забор крови. Заработная плата: оклад и премия 5000 рублей, компенсация жилья 15 000 рублей."),
            [],
        )

    def test_amount_in_thousands_is_read_from_text(self):
        (item,) = findings(duty="Проведение инъекций и забор крови. Заработная плата от 90 тыс. руб.")
        self.assertEqual((item.topic, item.kind), ("salary", "contradiction"))
        self.assertEqual(item.conflict[0], {"field": "salary_min, salary_max", "value": "45000–55000"})
        self.assertIn("90 тыс", item.conflict[1]["value"])

    def test_amount_in_the_next_sentence_is_not_the_salary(self):
        self.assertEqual(kinds(duty="Проведение инъекций. Оплата труда своевременно. Стоимость медосмотра 90000 рублей."), [])

    def test_one_bound_in_fields_is_not_compared_with_a_plain_amount(self):
        self.assertEqual(
            kinds(salary="от 45000", salary_max=0, duty="Проведение инъекций и забор крови. Заработная плата 90000 рублей."),
            [],
        )

    def test_wide_range_without_explanation(self):
        self.assertEqual(kinds(salary="от 30000", salary_min=30000, salary_max=120000), [("salary", "ambiguity")])
        self.assertEqual(
            kinds(salary="от 30000", salary_min=30000, salary_max=120000, duty="Проведение инъекций и забор крови. Размер оплаты зависит от категории."),
            [],
        )


class ScheduleTest(unittest.TestCase):
    def test_full_day_is_concrete_enough(self):
        self.assertEqual(kinds(schedule="Полный рабочий день"), [])

    def test_missing_schedule_is_gap(self):
        self.assertEqual(kinds(schedule=None), [("schedule", "gap")])

    def test_shift_work_without_timetable(self):
        (item,) = findings(schedule="Сменная работа", shift=["По графику сменности"])
        self.assertEqual((item.topic, item.kind), ("schedule", "ambiguity"))
        self.assertEqual(item.quote, "Сменная работа, По графику сменности")

    def test_shift_work_with_timetable_in_text(self):
        for text in ("График 2/2 с 8:00 до 20:00", "сутки через трое", "смены по 12 часов в день", "5-дневная рабочая неделя"):
            with self.subTest(text=text):
                self.assertEqual(kinds(schedule="Сменная работа", duty=f"Проведение инъекций и забор крови. {text}."), [])

    def test_timetable_in_schedule_comment(self):
        self.assertEqual(kinds(schedule="Сменная работа", scheduleTypeComment="08.00 - 17.00 ПН-ПТ"), [])

    def test_timetable_written_into_the_schedule_field(self):
        for schedule in ("Сменный график: 2/2, 08:00–20:00", "Понедельник–пятница, 10:00–19:00", "Полный рабочий день, 5/2"):
            with self.subTest(schedule=schedule):
                self.assertEqual(kinds(schedule=schedule), [])
        self.assertEqual(kinds(schedule="Сменный график"), [("schedule", "ambiguity")])

    def test_flexible_schedule_offered_next_to_a_fixed_one(self):
        (item,) = findings(
            schedule="Гибкий график",
            qualification="Пятидневная рабочая неделя с 09:00 до 18:00. Возможен гибкий график без уточнения правил.",
        )
        self.assertEqual((item.topic, item.kind, item.field), ("schedule", "ambiguity", "qualification"))
        self.assertEqual(item.quote, "Возможен гибкий график")

    def test_flexible_field_with_timetable_elsewhere_is_fine(self):
        self.assertEqual(kinds(schedule="Гибкий график", scheduleTypeComment="08.00 - 17.00 ПН-ПТ"), [])

    def test_flexible_schedule_from_the_task_statement(self):
        (item,) = findings(schedule=None, duty="Проведение инъекций и забор крови. Гибкий график.")
        self.assertEqual((item.topic, item.kind, item.field, item.quote), ("schedule", "ambiguity", "duty", "Гибкий график."))
        self.assertEqual(kinds(schedule=None, duty="Проведение инъекций. Гибкий график: с 9 до 18, пн-пт."), [])

    def test_flexible_schedule_field(self):
        self.assertEqual(kinds(schedule="Режим гибкого рабочего времени"), [("schedule", "ambiguity")])

    def test_vague_words_in_text_next_to_a_plain_schedule_field(self):
        (item,) = findings(duty="Проведение инъекций и забор крови. График работы по согласованию.")
        self.assertEqual((item.topic, item.kind, item.field), ("schedule", "ambiguity", "duty"))
        self.assertIn("График работы по согласованию", item.quote)

    def test_part_time_without_hours(self):
        self.assertEqual(
            kinds(schedule="Неполный рабочий день/неполная рабочая неделя", employment="Частичная занятость"),
            [("schedule", "ambiguity")],
        )
        self.assertEqual(
            kinds(schedule="Неполный рабочий день/неполная рабочая неделя", employment="Частичная занятость", qualification="Вакансия на 0,5 ставки"),
            [],
        )

    def test_full_day_against_shifts_in_text(self):
        (item,) = findings(duty="Проведение инъекций и забор крови. График работы сменный.")
        self.assertEqual((item.topic, item.kind), ("schedule", "contradiction"))
        self.assertEqual(item.conflict[0], {"field": "schedule", "value": "Полный рабочий день"})
        self.assertEqual(item.conflict[1]["field"], "duty")

    def test_five_day_week_does_not_contradict_full_day(self):
        self.assertEqual(kinds(duty="Проведение инъекций и забор крови. График 5/2."), [])


class LocationTest(unittest.TestCase):
    def test_missing_address_is_gap(self):
        self.assertEqual(kinds(addresses=None), [("location", "gap")])
        self.assertEqual(kinds(addresses={"address": []}), [("location", "gap")])

    def test_remote_work_needs_no_address(self):
        self.assertEqual(kinds(addresses=None, duty="Проведение консультаций по телефону. Удалённая работа."), [])

    def test_address_without_house(self):
        (item,) = findings(addresses={"address": [{"location": "Калининградская область, г Калининград"}]})
        self.assertEqual((item.topic, item.kind, item.field), ("location", "ambiguity", "addresses.address[0].location"))
        self.assertEqual(item.quote, "Калининградская область, г Калининград")
        self.assertIn("только населённый пункт", item.problem)

    def test_street_without_house(self):
        (item,) = findings(addresses={"address": [{"location": "г Калининград, Московский проспект"}]})
        self.assertIn("нет номера дома", item.problem)

    def test_fully_remote_against_required_presence(self):
        (item,) = findings(
            duty="Приём звонков и регистрация обращений. Работа полностью дистанционная из любого города.",
            qualification="Обязательное ежедневное присутствие в офисе в Калининграде к 09:00.",
        )
        self.assertEqual((item.topic, item.kind, item.field), ("location", "contradiction", "duty"))
        self.assertEqual(item.conflict[0], {"field": "duty", "value": "полностью дистанционная"})
        self.assertEqual(item.conflict[1]["field"], "qualification")
        self.assertIn("присутствие в офисе", item.conflict[1]["value"])

    def test_partly_remote_work_does_not_contradict_the_office(self):
        self.assertEqual(
            kinds(duty="Сопровождение информационных систем. Частично возможна удалённая работа.", qualification="Работа в офисе."),
            [],
        )


class EmploymentTest(unittest.TestCase):
    def test_missing_type_is_gap_unless_text_says_it(self):
        self.assertEqual(kinds(employment=None), [("employment", "gap")])
        self.assertEqual(kinds(employment=None, duty="Проведение инъекций и забор крови. Полная занятость."), [])

    def test_full_employment_with_part_time_schedule(self):
        result = findings(employment="Полная занятость", schedule="Неполный рабочий день/неполная рабочая неделя")
        conflict = [item for item in result if item.kind == "contradiction"]
        self.assertEqual(len(conflict), 1)
        self.assertEqual(
            conflict[0].conflict,
            [
                {"field": "employment", "value": "Полная занятость"},
                {"field": "schedule", "value": "Неполный рабочий день/неполная рабочая неделя"},
            ],
        )

    def test_part_time_with_part_time_schedule_agrees(self):
        result = kinds(employment="Частичная занятость", schedule="Неполный рабочий день/неполная рабочая неделя")
        self.assertNotIn(("employment", "contradiction"), result)

    def test_part_time_with_full_day(self):
        self.assertEqual(kinds(employment="Частичная занятость"), [("employment", "contradiction")])

    def test_full_employment_with_half_rate_in_text(self):
        self.assertEqual(kinds(qualification="Вакансия на 0,5 ставки"), [("employment", "contradiction")])

    def test_temporary_work_without_term(self):
        self.assertEqual(kinds(employment="Временная работа"), [("employment", "ambiguity")])
        self.assertEqual(kinds(employment="Временная работа", duty="Проведение инъекций и забор крови. Работа на 3 месяца."), [])


class WordingTest(unittest.TestCase):
    def test_no_legal_conclusions_in_rule_texts(self):
        from transparent_vacancy.ai_review import _LEGAL

        for item in rules.analyze(Vacancy({"id": "x"}))[0] + findings(
            employment="Полная занятость", schedule="Неполный рабочий день", salary_min=9, salary_max=1
        ):
            self.assertIsNone(_LEGAL.search(item.problem + " " + item.recommendation), item.problem)


if __name__ == "__main__":
    unittest.main()
