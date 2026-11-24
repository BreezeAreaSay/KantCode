import unittest

from tests.helpers import NO_TYPE, vacancy
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
        (item,) = findings(**NO_TYPE)
        self.assertEqual((item.field, item.quote, item.kind), ("employment", "", "gap"))
        self.assertTrue(item.problem and item.recommendation)


class TitleTest(unittest.TestCase):
    def test_generic_title_is_ambiguous(self):
        self.assertEqual(kinds(job__name="Специалист"), [("title", "ambiguity")])

    def test_entry_level_title_against_years_of_experience(self):
        (item,) = findings(
            job__name="Младший аналитик данных",
            requirement={"education": "Высшее", "experience": 5},
            requirements="Знание SQL.",
            duty="Подготовка SQL-отчётов, очистка таблиц, контроль показателей.",
        )
        self.assertEqual((item.topic, item.kind, item.quote), ("title", "contradiction", "Младший аналитик данных"))
        self.assertEqual(item.conflict[1], {"field": "requirement.experience", "value": "5"})
        from_text = findings(job__name="Стажёр отдела кадров", requirements="Опыт работы в кадровой службе не менее 4 лет.")
        self.assertEqual([(entry.topic, entry.kind) for entry in from_text], [("title", "contradiction"), ("requirements", "contradiction")])
        self.assertIn("не менее 4 лет", from_text[0].conflict[1]["value"])

    def test_entry_level_title_with_little_experience_is_fine(self):
        self.assertEqual(kinds(job__name="Младший воспитатель"), [])
        self.assertEqual(kinds(job__name="Младшая медицинская сестра", requirements="Желателен опыт работы от 3 лет"), [])

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

    def test_open_list_only_when_it_closes_the_list(self):
        self.assertEqual(
            kinds(duty="На основе анализа моделей и алгоритмов решения экономических и других задач разрабатывает программы."),
            [],
        )
        self.assertEqual(
            kinds(duty="Планирование работы отдела, контроль сроков. Осуществление иных функций, непосредственно связанных с задачами отдела."),
            [],
        )
        (item,) = findings(duty="Ведение документации, приём граждан, а также иные поручения, если потребуется.")
        self.assertIn("иные поручения", item.quote)

    def test_reference_to_a_job_description_next_to_real_duties_is_fine(self):
        self.assertEqual(
            kinds(duty="Выполнение работы в соответствии с должностными обязанностями. Перевозка пассажиров по регулярным маршрутам города."),
            [],
        )

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

    def test_unfilled_education_is_fine_when_the_employer_trains(self):
        for duty in ("Выпечка хлеба из полуфабрикатов. Остальному научим!", "Выкладка товара. Опыт работы не требуется, мы обучим."):
            with self.subTest(duty=duty):
                self.assertEqual(
                    kinds(requirement={"education": "Не указано", "experience": 0}, requirements="", qualification="", duty=duty), []
                )

    def test_experience_written_as_from_n_years(self):
        self.assertEqual(kinds(requirement={"education": "Высшее", "experience": "от 0"}, requirements="Внимательность.", qualification=""), [])
        self.assertEqual(Vacancy(vacancy(requirement={"experience": "от 3 лет"})).number("requirement.experience"), 3.0)

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

    def test_rotation_numbers_or_a_list_of_options_do_not_contradict_full_day(self):
        for duty in (
            "Работа за кассой и в торговом зале. График работы 2/2.",
            "Работа за кассой. График работы 5/2, 2/2, возможен неполный рабочий день.",
            "Приготовление блюд. Сменный график, пятидневная рабочая неделя.",
        ):
            with self.subTest(duty=duty):
                self.assertNotIn(("schedule", "contradiction"), kinds(duty=duty))

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
        (item,) = findings(
            duty="Сопровождение информационных систем. Частично возможна удалённая работа.", qualification="Работа в офисе."
        )
        self.assertEqual((item.topic, item.kind), ("location", "ambiguity"))  # неясно сколько, но противоречия нет

    def test_city_in_the_text_against_the_address(self):
        (item,) = findings(duty="Ведение кадрового делопроизводства. Работа в офисе в г. Светлогорске.")
        self.assertEqual((item.topic, item.kind, item.field), ("location", "contradiction", "duty"))
        self.assertEqual(item.conflict[0], {"field": "duty", "value": "Работа в офисе в г. Светлогорске"})
        self.assertEqual(item.conflict[1]["field"], "addresses.address[0].location")
        self.assertIn("г Калининград", item.conflict[1]["value"])

    def test_city_that_agrees_with_the_address_or_is_not_the_workplace(self):
        for duty in (
            "Ведение кадрового делопроизводства. Работа в офисе в г. Калининграде.",
            "Проведение инъекций и забор крови. Имеется служебный автобус с г. Гвардейск.",
            "Проведение инъекций и забор крови. Доставка анализов в г. Светлогорск.",
        ):
            with self.subTest(duty=duty):
                self.assertEqual(kinds(duty=duty), [])
        self.assertEqual(
            kinds(duty="Проведение консультаций. Удалённая работа, офис в г. Светлогорске.", addresses=None), []
        )


class WordingRulesTest(unittest.TestCase):
    """Оговорки в тексте, которые правила находят без модели."""

    def test_possible_trips_overtime_and_remote_work(self):
        cases = (
            ("Перевозка сотрудников по городу. Возможны командировки.", "duties", "Возможны командировки"),
            ("Работа с кассой, запись на занятия. Периодически дежурства.", "schedule", "Периодически дежурства"),
            ("Приготовление горячих блюд. Работа в дневное время, иногда в выходные.", "schedule", "иногда в выходные"),
            ("Администрирование сети учреждения. Частично возможна удалённая работа.", "location", "возможна удалённая работа"),
        )
        for duty, topic, quote in cases:
            with self.subTest(duty=duty):
                (item,) = findings(duty=duty)
                self.assertEqual((item.topic, item.kind, item.field, item.quote), (topic, "ambiguity", "duty", quote))
                self.assertIn("как часто", item.problem)

    def test_condition_put_off_until_the_interview_goes_to_its_topic(self):
        cases = (
            ("Формат и адрес рабочего места уточняются при собеседовании.", "location"),
            ("Требования к знаниям уточняются на собеседовании.", "requirements"),
            ("Формат работы обсуждается.", "location"),
        )
        for text, topic in cases:
            with self.subTest(text=text):
                (item,) = findings(qualification=text)
                self.assertEqual((item.topic, item.kind, item.field), (topic, "ambiguity", "qualification"))
                self.assertEqual(item.quote, text.rstrip("."))
                self.assertIn("отложено до собеседования", item.problem)

    def test_phrase_about_everything_goes_to_the_topic_of_its_field_once(self):
        (item,) = findings(qualification="Условия обсуждаются на собеседовании.")
        self.assertEqual((item.topic, item.field), ("requirements", "qualification"))
        # если по требованиям замечание уже есть, общая фраза второго не добавляет
        both = kinds(
            requirement={"education": "Не указано", "experience": 0},
            requirements="Ответственность.",
            qualification="Условия обсуждаются на собеседовании.",
        )
        self.assertEqual(both, [("requirements", "gap")])

    def test_put_off_schedule_or_pay_is_fine_when_the_fields_are_concrete(self):
        self.assertEqual(kinds(duty="Проведение инъекций и забор крови. График уточняется на собеседовании: 5/2, с 9 до 18."), [])
        self.assertEqual(kinds(duty="Проведение инъекций и забор крови. Оплата обсуждается на собеседовании."), [])

    def test_one_finding_for_one_phrase(self):
        (item,) = findings(duty="Сопровождение систем. Формат работы обсуждается, частично возможна удалённая работа.")
        self.assertEqual((item.topic, item.quote), ("location", "Формат работы обсуждается, частично возможна удалённая работа"))

    def test_plain_statements_are_not_touched(self):
        for duty in (
            "Проведение занятий по лечебной физкультуре для детей в группах и индивидуально.",
            "Выдача книг. График работы 5/2, возможен неполный рабочий день.",
            "Командировки по области два раза в месяц на один день.",
        ):
            with self.subTest(duty=duty):
                self.assertEqual(kinds(duty=duty), [])


class EmploymentTest(unittest.TestCase):
    def test_missing_type_is_gap_unless_text_says_it(self):
        self.assertEqual(kinds(**NO_TYPE), [("employment", "gap")])
        # поле графика «полный/неполный рабочий день» уже говорит о занятости: портал часто не отдаёт её поле,
        # поэтому это не замечание, а пометка вне findings
        self.assertEqual(kinds(employment=None), [])
        findings_, facts = rules.analyze(Vacancy(vacancy(employment=None)))
        (note,) = facts.notes
        self.assertEqual((note.topic, note.kind, note.field, note.quote), ("employment", "note", "employment", ""))
        self.assertIn("«Полный рабочий день»", note.problem)
        self.assertEqual(rules.analyze(Vacancy(vacancy()))[1].notes, [])
        self.assertEqual(kinds(employment=None, schedule="Режим гибкого рабочего времени", scheduleTypeComment="08.00 - 17.00 ПН-ПТ"), [("employment", "gap")])
        self.assertEqual(kinds(**NO_TYPE, duty="Проведение инъекций и забор крови. Полная занятость."), [])

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
