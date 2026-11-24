import unittest

from tests.helpers import NO_TYPE, vacancy
from transparent_vacancy import ai_review, rules
from transparent_vacancy.llm import AIError, parse_json_object
from transparent_vacancy.vacancy import Vacancy

PROBLEM = "Из формулировки нельзя понять, как часто и куда придётся ездить."
ADVICE = "Уточните у работодателя, как часто бывают поездки и кто их оплачивает."


def item(**changes) -> dict:
    data = {
        "topic": "duties",
        "type": "ambiguity",
        "field": "duty",
        "quote": "возможны подмены",
        "second_field": "",
        "second_quote": "",
        "problem": PROBLEM,
        "recommendation": ADVICE,
    }
    data.update(changes)
    return data


def validate(items, **changes):
    subject = Vacancy(vacancy(**changes))
    known, facts = rules.analyze(subject)
    return ai_review.validate(items, subject, known, facts)


DUTY = "Проведение инъекций и забор крови. Возможны подмены. Работа в дружном коллективе."


class ValidateTest(unittest.TestCase):
    def test_finding_with_real_quote_is_accepted_with_source_text(self):
        (found,) = validate([item()], duty=DUTY)
        self.assertEqual((found.topic, found.kind, found.field, found.detected_by), ("duties", "ambiguity", "duty", "ai"))
        self.assertEqual(found.quote, "Возможны подмены")  # как в источнике, а не как написала модель
        self.assertEqual(found.recommendation, ADVICE)

    def test_invented_quote_is_dropped(self):
        self.assertEqual(validate([item(quote="удобный график", topic="schedule")], duty=DUTY), [])

    def test_quote_is_found_in_another_field_when_model_names_the_wrong_one(self):
        (found,) = validate([item(field="requirements")], duty=DUTY)
        self.assertEqual(found.field, "duty")

    def test_unknown_topic_type_and_gaps_are_dropped(self):
        self.assertEqual(validate([item(topic="benefits")], duty=DUTY), [])
        self.assertEqual(validate([item(type="gap", quote="")], duty=DUTY), [])
        self.assertEqual(validate([item(quote="")], duty=DUTY), [])

    def test_legal_conclusion_is_dropped(self):
        bad = item(problem="Такая формулировка является нарушением трудового законодательства.")
        self.assertEqual(validate([bad], duty=DUTY), [])

    def test_recommendation_with_invented_number_or_law_is_replaced(self):
        for advice in ("Уточните, верно ли, что подмены длятся 14 дней.", "Сошлитесь на ТК РФ и уточните условия."):
            with self.subTest(advice=advice):
                (found,) = validate([item(recommendation=advice)], duty=DUTY)
                self.assertEqual(found.recommendation, ai_review._FALLBACK_RECOMMENDATION["duties"])

    def test_problem_about_a_phrase_absent_from_the_vacancy_is_dropped(self):
        bad = item(quote="Проведение инъекций", problem="Фраза «удобный график» не конкретизирована и непонятна.")
        self.assertEqual(validate([bad], duty=DUTY), [])

    def test_truncated_or_foreign_problem_is_dropped(self):
        self.assertEqual(validate([item(problem="Фраза ")], duty=DUTY), [])
        self.assertEqual(validate([item(problem="The phrase is vague and unclear for applicants.")], duty=DUTY), [])
        self.assertEqual(validate([item(problem=PROBLEM + " 不清楚")], duty=DUTY), [])

    def test_vague_phrase_is_not_a_problem_when_topic_is_concrete(self):
        text = "Проведение инъекций и забор крови. Достойная оплата. Удобный график."
        salary = item(topic="salary", quote="Достойная оплата")
        schedule = item(topic="schedule", quote="Удобный график")
        self.assertEqual(validate([salary, schedule], duty=text, scheduleTypeComment="с 8 до 17, пн-пт"), [])

    def test_side_condition_is_kept_even_when_topic_is_concrete(self):
        trips = item(topic="schedule")  # подмены — не общие слова о графике
        (found,) = validate([trips], duty=DUTY, scheduleTypeComment="с 8 до 17, пн-пт")
        self.assertEqual((found.topic, found.quote), ("schedule", "Возможны подмены"))

    def test_ai_does_not_repeat_rules(self):
        text = "Ведение документации и другие поручения руководителя. Возможны подмены."
        duplicate = item(quote="другие поручения руководителя")
        self.assertEqual(validate([duplicate], duty=text), [])
        after_gap = item(topic="employment", quote="Возможны подмены")
        self.assertEqual(validate([after_gap], **NO_TYPE, duty=DUTY), [])

    def test_ai_may_add_another_place_on_a_topic_the_rules_touched(self):
        text = "Ведение документации и другие поручения руководителя. Возможны подмены."
        (found,) = validate([item()], duty=text)
        self.assertEqual(found.quote, "Возможны подмены")

    def test_strict_mode_needs_a_hedge_in_the_quote(self):
        plain = item(quote="Проведение инъекций и забор крови", problem="Не сказано, какие именно действия входят в эту работу.")
        self.assertEqual(validate([plain], duty=DUTY), [])
        subject = Vacancy(vacancy(duty=DUTY))
        known, facts = rules.analyze(subject)
        self.assertEqual(len(ai_review.validate([plain], subject, known, facts, strict=False)), 1)

    def test_hedge_is_seen_in_every_form_of_the_word(self):
        for phrase in ("Возможен перевод в другой кабинет", "Возможна замена коллег", "Возможно совмещение"):
            with self.subTest(phrase=phrase):
                text = f"Проведение инъекций и забор крови. {phrase}."
                (found,) = validate([item(quote=phrase)], duty=text)
                self.assertEqual(found.quote, phrase)

    def test_problem_or_advice_quoting_words_absent_from_the_vacancy(self):
        invented = "Фраза «удобный график» не конкретизирована и непонятна."
        self.assertEqual(validate([item(problem=invented)], duty=DUTY), [])
        (found,) = validate([item(recommendation="Уточните, что значит «удобный график».")], duty=DUTY)
        self.assertEqual(found.recommendation, ai_review._FALLBACK_RECOMMENDATION["duties"])
        (found,) = validate([item(problem="Фраза «возможны подмены» не говорит, как часто придётся ездить.")], duty=DUTY)
        self.assertEqual(found.quote, "Возможны подмены")

    def test_long_quote_is_cut_at_a_word(self):
        text = "Возможны подмены " + "по районам области и соседним регионам " * 8
        (found,) = validate([item(quote=text)], duty=text)
        self.assertLessEqual(len(found.quote), ai_review.MAX_QUOTE_CHARS)
        self.assertTrue(found.quote.startswith("Возможны подмены по районам"))
        self.assertIn(found.quote, text)
        self.assertFalse(found.quote.endswith(" "))

    def test_address_named_loosely_is_looked_up_in_the_address_field(self):
        text = "Проведение инъекций. Приём граждан в г. Светлогорске."
        (found,) = validate(
            [
                item(
                    topic="location", type="contradiction", field="duty", quote="Приём граждан в г. Светлогорске",
                    second_field="address", second_quote="г Калининград",
                    problem="В описании назван один город, а в адресе места работы другой.",
                )
            ],
            duty=text,
        )
        self.assertEqual(found.conflict[1], {"field": "addresses.address[0].location", "value": "г Калининград"})

    def test_rule_findings_of_another_topic_or_kind_do_not_hide_ai_findings(self):
        # правило: адрес без дома (место работы, неоднозначность) и нет типа занятости (пропуск)
        changes = dict(
            duty="Проведение инъекций. Возможны подмены. Приём граждан в г. Светлогорске.",
            addresses={"address": [{"location": "Калининградская область, г Калининград"}]},
            **NO_TYPE,
        )
        clash = item(
            topic="location", type="contradiction", field="duty", quote="Приём граждан в г. Светлогорске",
            second_field="addresses.address[0].location", second_quote="г Калининград",
            problem="В описании назван один город, а в адресе места работы другой.",
        )
        found = validate([item(), clash, item(topic="employment", quote="Возможны подмены")], **changes)
        self.assertEqual([(entry.topic, entry.kind) for entry in found], [("duties", "ambiguity"), ("location", "contradiction")])

    def test_no_more_than_six_findings_per_vacancy(self):
        phrases = [
            "Возможны подмены", "Иногда помощь соседнему отделу", "Возможно обучение", "Иногда участие в совещаниях",
            "Возможна подмена коллег", "Иногда замена на посту", "Возможны изменения в задачах",
        ]
        topics = ["duties", "duties", "requirements", "requirements", "title", "title", "employment"]
        text = "Проведение инъекций. " + ". ".join(phrases) + "."
        found = validate([item(topic=topic, quote=phrase) for topic, phrase in zip(topics, phrases)], duty=text)
        self.assertEqual(len(found), ai_review.MAX_FINDINGS)
        self.assertEqual([entry.quote for entry in found], phrases[:6])

    def test_answer_of_the_wrong_shape_gives_nothing(self):
        for answer in (None, "замечаний нет", {"topic": "duties"}, [None, 5, "строка", []]):
            with self.subTest(answer=answer):
                self.assertEqual(validate(answer, duty=DUTY), [])

    def test_topic_aliases(self):
        (found,) = validate([item(topic="duty")], duty=DUTY)
        self.assertEqual(found.topic, "duties")

    def test_contradiction_needs_both_values(self):
        text = "Проведение инъекций. Приём граждан в г. Светлогорске."
        good = item(
            topic="location", type="contradiction", field="duty", quote="Приём граждан в г. Светлогорске",
            second_field="addresses.address[0].location", second_quote="г Калининград, Летняя улица",
            problem="В описании назван один город, а в адресе места работы другой.",
        )
        (found,) = validate([good], duty=text)
        self.assertEqual(found.kind, "contradiction")
        self.assertEqual(
            found.conflict,
            [
                {"field": "duty", "value": "Приём граждан в г. Светлогорске"},
                {"field": "addresses.address[0].location", "value": "г Калининград, Летняя улица"},
            ],
        )
        self.assertEqual(validate([dict(good, second_quote="г Москва")], duty=text), [])
        self.assertEqual(validate([dict(good, second_field="duty", second_quote="Приём граждан в г. Светлогорске")], duty=text), [])

    def test_zero_salary_bound_cannot_be_a_side_of_a_contradiction(self):
        claim = item(
            topic="salary", type="contradiction", field="salary_max", quote="0",
            second_field="salary_min", second_quote="35000",
            problem="Минимальная зарплата указана, а максимальная равна нулю, диапазон некорректен.",
        )
        self.assertEqual(validate([claim], salary="", salary_min=35000, salary_max=0), [])
        # и «0» из поля опыта не подставляется вместо отсутствующей границы зарплаты
        self.assertEqual(
            validate([claim], salary="", salary_min=35000, salary_max=0, requirement={"education": "Высшее", "experience": 0}),
            [],
        )

    def test_contradiction_between_sides_about_different_things_is_dropped(self):
        duty = "Осмотр и техническое обслуживание электрического оборудования, замена ламп."
        qualification = "Работа в службе внутреннего сервиса, обслуживание электрооборудования в подразделении свиноводства."
        claim = item(
            topic="duties", type="contradiction", field="duty", quote="Осмотр и техническое обслуживание электрического оборудования",
            second_field="qualification", second_quote="Работа в службе внутреннего сервиса",
            problem="Обязанности описаны иначе, чем подразделение, в котором предстоит работать.",
        )
        self.assertEqual(validate([claim], duty=duty, qualification=qualification), [])

    def test_contradictions_about_the_same_thing_are_kept(self):
        cases = (
            (dict(schedule="Гибкий график", qualification="Пятидневная рабочая неделя с 09:00 до 18:00."),
             "schedule", "schedule", "Гибкий график", "qualification", "Пятидневная рабочая неделя с 09:00 до 18:00"),
            (dict(requirement={"education": "Высшее", "experience": 0}, requirements="Опыт разработки учебных материалов."),
             "requirements", "requirements", "Опыт разработки учебных материалов", "requirement.experience", "0"),
            (dict(job__name="Младший аналитик данных", qualification="Требуется не менее 5 лет опыта работы аналитиком."),
             "title", "job-name", "Младший аналитик данных", "qualification", "не менее 5 лет опыта"),
        )
        for changes, topic, field, quote, second_field, second_quote in cases:
            with self.subTest(quote=quote):
                claim = item(
                    topic=topic, type="contradiction", field=field, quote=quote,
                    second_field=second_field, second_quote=second_quote,
                    problem="Два поля вакансии сообщают об этом по-разному, соискатель не поймёт, что верно.",
                )
                subject = Vacancy(vacancy(**changes))
                facts = rules.analyze(subject)[1]
                found = ai_review.validate([claim], subject, [], facts)
                self.assertEqual(len(found), 1)

    def test_limits_and_duplicates(self):
        text = "Проведение инъекций. Возможны подмены. Иногда помощь соседнему отделу. Прочие задачи отдела. Разное."
        many = [
            item(),
            item(),
            item(quote="Иногда помощь соседнему отделу"),
            item(quote="Прочие задачи отдела"),
        ]
        self.assertEqual(len(validate(many, duty=text)), ai_review.MAX_PER_TOPIC)

    def test_garbage_reply_gives_nothing(self):
        self.assertEqual(validate(None), [])
        self.assertEqual(validate("нет замечаний"), [])
        self.assertEqual(validate(["строка", 5, {}]), [])


class RequestTest(unittest.TestCase):
    def test_request_carries_fields_and_known_findings(self):
        import json

        subject = Vacancy(vacancy(**NO_TYPE))
        known, _ = rules.analyze(subject)
        request = json.loads(ai_review.build_request(subject, known))
        self.assertEqual(request["fields"]["job-name"], "Медицинская сестра процедурной")
        self.assertIn("addresses.address[0].location", request["fields"])
        self.assertEqual(request["known_findings"], [{"topic": "employment", "type": "gap", "field": "employment"}])

    def test_prompt_has_no_example_phrase_the_model_could_copy(self):
        self.assertNotIn("удобный график", ai_review.SYSTEM_PROMPT)


class ParseTest(unittest.TestCase):
    def test_json_inside_code_fence_or_prose(self):
        self.assertEqual(parse_json_object('```json\n{"findings": []}\n```'), {"findings": []})
        self.assertEqual(parse_json_object('Вот ответ: {"findings": []} Готово.'), {"findings": []})

    def test_not_json(self):
        with self.assertRaises(AIError):
            parse_json_object("замечаний нет")
        with self.assertRaises(AIError):
            parse_json_object('{"findings": [')


if __name__ == "__main__":
    unittest.main()
