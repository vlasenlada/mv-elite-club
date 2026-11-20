"""Human-readable publication checks around the existing model validators."""
from copy import copy

from django.core.exceptions import ValidationError
from django.urls import reverse

from .admin_forms import audience_errors


def publication_readiness(test):
    issues, warnings = [], []
    metadata = copy(test)
    metadata.is_published = False
    try:
        metadata.full_clean()
    except ValidationError as error:
        issues.extend({"message": message} for message in error.messages)
    issues.extend({"message": message} for message in audience_errors(test))
    questions = list(test.questions.select_related("passage").prefetch_related("options").order_by("order", "pk")) if test.pk else []
    active = [question for question in questions if question.is_active]
    if len(active) < test.question_count:
        issues.append({"message": f"Потрібно {test.question_count} активних запитань, зараз є {len(active)}. Додайте запитання або змініть кількість у спробі."})
    for question in active:
        try:
            question.validate_content()
        except ValidationError as error:
            issues.append({"message": f"Запитання {question.order} ({question.topic}): {'; '.join(error.messages)}", "url": reverse("admin:knowledge_question_change", args=[question.pk])})
    bands = list(test.result_bands.order_by("minimum_percent")) if test.pk else []
    if bands and bands[0].minimum_percent != 0:
        issues.append({"message": "Перший діапазон результатів має починатися з 0%."})
    if test.level_estimation_enabled and (not bands or any(not band.level_hint for band in bands)):
        issues.append({"message": "Для орієнтовної мовної оцінки заповніть рівні в усіх діапазонах результатів."})
    if not bands:
        warnings.append("Діапазони результатів не додані. Відвідувач отримає відсоток, пояснення й результати за темами без окремої текстової інтерпретації.")
    rules = list(test.recommendations.select_related("course")) if test.pk else []
    for rule in rules:
        rule.assessment = test
        try:
            rule.full_clean()
        except ValidationError as error:
            issues.append({"message": f"Рекомендація курсу «{rule.course}»: {'; '.join(error.messages)}"})
        if not rule.course.is_published:
            warnings.append(f"Курс «{rule.course}» прихований і не відображатиметься в рекомендаціях.")
    if not rules:
        warnings.append("Рекомендації курсів не додані. На результаті залишиться перехід до каталогу.")
    return {"ready": not issues, "issues": issues, "warnings": warnings, "questions": questions, "active_count": len(active)}
