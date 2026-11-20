import secrets
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import connections, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import Assessment, Attempt, CourseRecommendation, Option, Passage, Question, ResultBand
from .numbers import parse_number


def storage_ready():
    tables = set(connections["default"].introspection.table_names())
    return all(model._meta.db_table in tables for model in (Assessment, Attempt, Question, Option, Passage, ResultBand, CourseRecommendation))


def owner_digest(request, *, create=False):
    token = request.session.get("knowledge_owner")
    if not token and create:
        token = secrets.token_urlsafe(32)
        request.session["knowledge_owner"] = token
    return salted_hmac("knowledge-owner", token, algorithm="sha256").hexdigest() if token else None


def snapshot_for(test):
    test.validate_content()
    questions = []
    for question in test.questions.filter(is_active=True).select_related("passage").prefetch_related("options").order_by("order", "pk")[:test.question_count]:
        questions.append({
            "prompt": question.prompt, "kind": question.kind, "topic": question.topic,
            "explanation": question.explanation,
            "passage": {"title": question.passage.title, "text": question.passage.text} if question.passage else None,
            "numeric_answer": str(question.numeric_answer) if question.numeric_answer is not None else None,
            "tolerance": str(question.tolerance),
            "options": [{"id": str(option.pk), "text": option.text, "correct": option.is_correct} for option in question.options.all()],
        })
    return {
        "version": 1, "title": test.title, "subject": test.subject, "grade": test.grade,
        "band": test.effective_band, "audience": test.audience,
        "level_estimation_enabled": test.level_estimation_enabled,
        "questions": questions,
        "bands": [{"minimum": str(b.minimum_percent), "title": b.title, "feedback": b.feedback,
                   "level_hint": b.level_hint if test.level_estimation_enabled else ""} for b in test.result_bands.order_by("minimum_percent")],
        "recommendations": [{"course_id": rule.course_id, "title": rule.course.title,
                             "minimum": str(rule.minimum_percent), "maximum": str(rule.maximum_percent), "why": rule.explanation}
                            for rule in test.recommendations.select_related("course").filter(course__is_published=True)],
    }


@transaction.atomic
def start_attempt(test_id, request):
    test = Assessment.objects.select_for_update().get(pk=test_id, is_published=True)
    snapshot = snapshot_for(test)
    return Attempt.objects.create(assessment=test, user=request.user if request.user.is_authenticated else None,
                                  owner_digest=owner_digest(request, create=True), snapshot=snapshot,
                                  expires_at=timezone.now() + timedelta(hours=24))


def grade_snapshot(snapshot, answers):
    details, topics = [], defaultdict(lambda: {"correct": 0, "total": 0})
    correct_count = 0
    for index, question in enumerate(snapshot["questions"], 1):
        answer = answers.get(str(index))
        if question["kind"] == "numeric":
            correct = answer is not None and abs(parse_number(answer) - Decimal(question["numeric_answer"])) <= Decimal(question["tolerance"])
            selected = [answer] if answer is not None else []
            expected = [question["numeric_answer"]]
        else:
            answer = answer or []
            expected_ids = {option["id"] for option in question["options"] if option["correct"]}
            correct = set(answer) == expected_ids
            selected = [option["text"] for option in question["options"] if option["id"] in answer]
            expected = [option["text"] for option in question["options"] if option["correct"]]
        correct_count += int(correct)
        topics[question["topic"]]["correct"] += int(correct)
        topics[question["topic"]]["total"] += 1
        details.append({"prompt": question["prompt"], "passage": question["passage"], "selected": selected,
                        "expected": expected, "explanation": question["explanation"], "correct": bool(correct)})
    percentage = (Decimal(correct_count) * 100 / len(details)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    band = None
    for candidate in snapshot["bands"]:
        if percentage >= Decimal(candidate["minimum"]):
            band = candidate
    recommendations = []
    for rule in snapshot["recommendations"]:
        if Decimal(rule["minimum"]) <= percentage <= Decimal(rule["maximum"]) and rule["course_id"] not in {r["course_id"] for r in recommendations}:
            recommendations.append(rule)
    return {
        "correct": correct_count, "total": len(details), "percentage": str(percentage), "details": details,
        "topics": dict(topics), "strong": [name for name, values in topics.items() if values["correct"] == values["total"]],
        "repeat": [name for name, values in topics.items() if values["correct"] < values["total"]],
        "band": band, "recommendations": recommendations[:3],
    }
