from functools import wraps
from xml.etree import ElementTree as ET

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe
from django.views.decorators.debug import sensitive_post_parameters

from courses.models import Course
from users.account_security import allow_request
from .forms import AnswerForm
from .models import Assessment, Attempt
from .services import grade_snapshot, owner_digest, start_attempt, storage_ready


DISCLAIMER = "Результат є орієнтовною діагностикою окремих знань. Він не є сертифікатом CEFR і не прогнозує бал НМТ."


def private(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["Referrer-Policy"] = "same-origin"
        return response
    return wrapped


def unavailable(request):
    response = render(request, "knowledge/unavailable.html", status=503)
    response["X-Robots-Tag"] = "noindex, noarchive"
    return response


def limited():
    response = HttpResponse("Забагато запитів. Спробуйте пізніше.", status=429)
    response["Retry-After"] = "900"
    response["X-Robots-Tag"] = "noindex"
    return response


@require_safe
def catalog(request, subject=None, nmt=False):
    if subject not in {None, "english", "math"}:
        raise Http404
    if not storage_ready():
        return unavailable(request)
    tests = Assessment.objects.filter(is_published=True)
    if subject:
        tests = tests.filter(subject=subject)
    if nmt:
        tests = tests.filter(Q(grade_band="nmt") | Q(grade__gte=10))
    audiences = list(tests.exclude(audience="").order_by("audience").values_list("audience", flat=True).distinct())
    grade = request.GET.get("grade", "")
    if grade:
        if not grade.isascii() or not grade.isdigit() or not 1 <= int(grade) <= 11:
            return HttpResponse("Оберіть клас від 1 до 11.", status=400, headers={"X-Robots-Tag": "noindex"})
        number = int(grade)
        band = "primary" if number <= 4 else "middle" if number <= 9 else "nmt"
        tests = tests.filter(Q(grade=number) | Q(grade__isnull=True, grade_band=band))
    category = request.GET.get("category", "")
    if category in Assessment.Category.values:
        tests = tests.filter(category=category)
    audience = request.GET.get("audience", "")
    if audience in audiences:
        tests = tests.filter(audience=audience)
    label = "Англійська мова" if subject == "english" else "Математика" if subject else "Перевір свої знання"
    title = ("Діагностичні тести НМТ: " if nmt else "Безкоштовні тести: " if subject else "") + label
    response = render(request, "knowledge/catalog.html", {
        "tests": tests, "subject": subject, "nmt": nmt, "heading": title, "seo_title": title + " | M&V Knowledge Check",
        "seo_description": title + ". Пройди тест без реєстрації, отримай пояснення й рекомендації навчання.",
        "canonical": request.build_absolute_uri(request.path), "disclaimer": DISCLAIMER,
        "categories": [] if subject == "math" else Assessment.Category.choices, "grades": range(1, 12), "audiences": audiences,
        "selected_grade": grade, "selected_category": category, "selected_audience": audience,
        "filtered": bool(request.GET),
    })
    if request.GET:
        response["X-Robots-Tag"] = "noindex, follow"
    return response


@require_safe
def detail(request, subject, slug):
    if not storage_ready():
        return unavailable(request)
    test = get_object_or_404(Assessment, subject=subject, slug=slug, is_published=True)
    return render(request, "knowledge/detail.html", {
        "test": test, "seo_title": test.seo_title or test.title + " | M&V Knowledge Check",
        "seo_description": test.seo_description or test.description[:280] or "Безкоштовна перевірка знань із поясненнями відповідей.",
        "canonical": request.build_absolute_uri(test.get_absolute_url()), "disclaimer": DISCLAIMER,
    })


@private
@require_POST
def start(request, subject, slug):
    if not storage_ready():
        return unavailable(request)
    test = get_object_or_404(Assessment, subject=subject, slug=slug, is_published=True)
    if not allow_request("knowledge-start-ip", request.META.get("REMOTE_ADDR", ""), limit=10, seconds=900):
        return limited()
    try:
        attempt = start_attempt(test.pk, request)
    except (ValidationError, Assessment.DoesNotExist):
        return HttpResponse("Тест оновлюється. Поверніться до каталогу й спробуйте пізніше.", status=409)
    return redirect("knowledge:question", attempt_id=attempt.pk, number=1)


def owned(request, attempt_id, *, lock=False):
    query = Attempt.objects.select_for_update() if lock else Attempt.objects.all()
    ownership = Q(pk__in=[])
    digest = owner_digest(request)
    if digest:
        ownership |= Q(owner_digest=digest)
    if request.user.is_authenticated and request.user.is_active:
        ownership |= Q(user=request.user)
    return get_object_or_404(query.filter(ownership), pk=attempt_id)


def expired(attempt):
    return not attempt.completed_at and attempt.expires_at <= timezone.now()


@private
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("answer")
@transaction.atomic
def question(request, attempt_id, number):
    if not storage_ready():
        return unavailable(request)
    attempt = owned(request, attempt_id, lock=request.method == "POST")
    if attempt.completed_at:
        return redirect("knowledge:result", attempt_id=attempt.pk)
    if expired(attempt):
        return render(request, "knowledge/expired.html", status=410)
    total = len(attempt.snapshot["questions"])
    if not 1 <= number <= total:
        raise Http404
    item = attempt.snapshot["questions"][number - 1]
    initial = attempt.answers.get(str(number))
    if item["kind"] == "single":
        initial = initial[0] if initial else ""
    form = AnswerForm(request.POST if request.method == "POST" else None, question=item, initial={"answer": initial})
    if request.method == "POST":
        if not allow_request("knowledge-answer", attempt.owner_digest, limit=300, seconds=3600):
            return limited()
        if form.is_valid():
            attempt.answers[str(number)] = form.cleaned_data["answer"]
            attempt.save(update_fields=["answers"])
            move = request.POST.get("move")
            if move == "previous" and number > 1:
                return redirect("knowledge:question", attempt_id=attempt.pk, number=number - 1)
            if move == "next" and number < total:
                return redirect("knowledge:question", attempt_id=attempt.pk, number=number + 1)
            return redirect("knowledge:summary", attempt_id=attempt.pk)
    # Do not pass the snapshot, correct flags, numeric answer or explanations to HTML.
    public_question = {key: item[key] for key in ("prompt", "kind", "topic", "passage")}
    answered = sum(bool(value) for value in attempt.answers.values())
    return render(request, "knowledge/question.html", {
        "form": form, "item": public_question, "attempt_id": attempt.pk, "number": number, "total": total,
        "title": attempt.snapshot["title"], "answered": answered, "progress": round(answered * 100 / total),
        "has_previous": number > 1, "has_next": number < total, "restore_draft": request.method == "GET",
    })


@private
@require_http_methods(["GET", "POST"])
@transaction.atomic
def summary(request, attempt_id):
    if not storage_ready():
        return unavailable(request)
    attempt = owned(request, attempt_id, lock=request.method == "POST")
    if attempt.completed_at:
        return redirect("knowledge:result", attempt_id=attempt.pk)
    if expired(attempt):
        return render(request, "knowledge/expired.html", status=410)
    if request.method == "POST":
        if not allow_request("knowledge-finish", attempt.owner_digest, limit=30, seconds=900):
            return limited()
        attempt.result = grade_snapshot(attempt.snapshot, attempt.answers)
        attempt.percentage = attempt.result["percentage"]
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["result", "percentage", "completed_at"])
        return redirect("knowledge:result", attempt_id=attempt.pk)
    rows = [{"number": index, "answered": bool(attempt.answers.get(str(index)))} for index in range(1, len(attempt.snapshot["questions"]) + 1)]
    return render(request, "knowledge/summary.html", {"rows": rows, "title": attempt.snapshot["title"], "attempt_id": attempt.pk})


@private
@require_safe
def result(request, attempt_id):
    if not storage_ready():
        return unavailable(request)
    attempt = owned(request, attempt_id)
    if not attempt.completed_at:
        return redirect("knowledge:summary", attempt_id=attempt.pk)
    recommendations = []
    for stored in attempt.result["recommendations"]:
        course = Course.objects.filter(pk=stored["course_id"], is_published=True, subject=attempt.snapshot["subject"]).first()
        if course and (not attempt.snapshot["band"] or course.grade_band == attempt.snapshot["band"]):
            recommendations.append({"course": course, "why": stored["why"]})
    return render(request, "knowledge/result.html", {
        "result": attempt.result, "title": attempt.snapshot["title"], "audience": attempt.snapshot["audience"],
        "attempt_id": attempt.pk, "recommendations": recommendations, "subject": attempt.snapshot["subject"],
        "disclaimer": DISCLAIMER,
    })


@require_safe
def sitemap(request):
    if not storage_ready():
        return HttpResponse(status=503, headers={"X-Robots-Tag": "noindex"})
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", namespace)
    root = ET.Element("{" + namespace + "}urlset")
    paths = [(reverse("knowledge:index"), None)]
    for subject in ("english", "math"):
        paths.extend([(reverse("knowledge:subject", args=[subject]), None), (reverse("knowledge:nmt", args=[subject]), None)])
    paths += [(test.get_absolute_url(), test.updated_at.date().isoformat()) for test in Assessment.objects.filter(is_published=True)]
    for path, updated in paths:
        entry = ET.SubElement(root, "{" + namespace + "}url")
        ET.SubElement(entry, "{" + namespace + "}loc").text = request.build_absolute_uri(path)
        if updated:
            ET.SubElement(entry, "{" + namespace + "}lastmod").text = updated
    return HttpResponse(ET.tostring(root, encoding="utf-8", xml_declaration=True), content_type="application/xml")
