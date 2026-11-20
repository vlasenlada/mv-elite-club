"""Strict JSON import: new drafts only, no overwrites or executable content."""
import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Assessment, CourseRecommendation, Option, Passage, Question
from .numbers import parse_number


def only_keys(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValidationError("Непідтримувані поля або структура JSON.")


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValidationError("Повторний ключ у JSON.")
        value[key] = item
    return value


def read_bank(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValidationError("JSON завеликий: максимум 2 МБ.")
    bank = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValidationError("Некоректне число JSON.")))
    only_keys(bank, {"version", "tests"})
    if bank.get("version") != 1 or not isinstance(bank.get("tests"), list) or not 1 <= len(bank["tests"]) <= 50:
        raise ValidationError("Потрібні version=1 і від 1 до 50 тестів.")
    seen = set()
    for test in bank["tests"]:
        only_keys(test, {"title", "slug", "subject", "description", "category", "grade_band", "grade", "audience", "estimated_minutes", "source_notes", "passages", "questions", "recommendations"})
        key = (test.get("subject"), test.get("slug"))
        if key in seen or key[0] not in Assessment.Subject.values or not key[1]:
            raise ValidationError("Перевірте предмет і унікальні адреси тестів.")
        seen.add(key)
        questions = test.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 100:
            raise ValidationError("У JSON кожен тест має містити 1–100 завдань.")
        metadata = {key: value for key, value in test.items() if key not in {"questions", "passages", "recommendations"}}
        Assessment(**metadata, question_count=len(questions)).full_clean(validate_unique=False, validate_constraints=False)
        if not isinstance(test.get("passages", []), list) or not isinstance(test.get("recommendations", []), list):
            raise ValidationError("Тексти й рекомендації мають бути списками.")
        passage_keys = set()
        for passage in test.get("passages", []):
            only_keys(passage, {"key", "title", "text"})
            if not isinstance(passage.get("key"), str) or not passage["key"] or passage["key"] in passage_keys or not isinstance(passage.get("text"), str) or not passage["text"].strip():
                raise ValidationError("Перевірте ключі й тексти для читання.")
            passage_keys.add(passage["key"])
        for question in questions:
            only_keys(question, {"prompt", "topic", "kind", "difficulty", "explanation", "passage", "numeric_answer", "tolerance", "options"})
            if any(not isinstance(question.get(key), str) or not question[key].strip() for key in ("prompt", "explanation", "topic")):
                raise ValidationError("Кожне завдання потребує запитання, теми й пояснення.")
            if question.get("passage") and question["passage"] not in passage_keys:
                raise ValidationError("Невідомий текст для читання.")
            if question.get("kind", "single") == "numeric":
                if isinstance(question.get("numeric_answer"), (bool, float)):
                    raise ValidationError("Правильне число передавайте рядком або цілим числом.")
                parse_number(question.get("numeric_answer", ""))
                if parse_number(question.get("tolerance", "0")) < 0 or question.get("options"):
                    raise ValidationError("Числове завдання потребує невід’ємної похибки, без варіантів.")
            else:
                options = question.get("options", [])
                if not isinstance(options, list) or not 2 <= len(options) <= 10:
                    raise ValidationError("Потрібно 2–10 варіантів.")
                for option in options:
                    only_keys(option, {"text", "is_correct"})
                    if not isinstance(option.get("text"), str) or not option["text"].strip() or type(option.get("is_correct")) is not bool:
                        raise ValidationError("Варіант потребує тексту та is_correct=true/false.")
                correct = sum(option["is_correct"] for option in options)
                kind = question.get("kind", "single")
                if kind not in {"single", "multiple"} or (kind == "single" and correct != 1) or (kind == "multiple" and correct < 2):
                    raise ValidationError("Некоректний тип або кількість правильних відповідей.")
        for rule in test.get("recommendations", []):
            only_keys(rule, {"course_id", "minimum_percent", "maximum_percent", "explanation"})
            if type(rule.get("course_id")) is not int:
                raise ValidationError("Для рекомендації потрібен цілий course_id.")
    return bank


@transaction.atomic
def import_bank(bank):
    created = []
    for data in bank["tests"]:
        if Assessment.objects.filter(subject=data["subject"], slug=data["slug"]).exists():
            raise ValidationError("Тест із такою адресою вже існує. Імпорт не перезаписує матеріали.")
        fields = {key: value for key, value in data.items() if key not in {"questions", "passages", "recommendations"}}
        test = Assessment.objects.create(**fields, question_count=len(data["questions"]), is_published=False)
        passages = {p["key"]: Passage.objects.create(assessment=test, title=p.get("title", ""), text=p["text"]) for p in data.get("passages", [])}
        for index, item in enumerate(data["questions"], 1):
            question = Question.objects.create(assessment=test, order=index, prompt=item["prompt"], topic=item["topic"],
                kind=item.get("kind", "single"), difficulty=item.get("difficulty", "easy"), explanation=item["explanation"],
                passage=passages.get(item.get("passage")), numeric_answer=parse_number(item["numeric_answer"]) if item.get("kind") == "numeric" else None,
                tolerance=parse_number(item.get("tolerance", "0")))
            for order, option in enumerate(item.get("options", []), 1):
                Option.objects.create(question=question, order=order, **option)
        for rule in data.get("recommendations", []):
            CourseRecommendation.objects.create(assessment=test, **rule)
        test.validate_content()
        created.append(test)
    return created
