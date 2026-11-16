from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_safe

from courses.models import Course
from users.account_security import allow_request, normalize_email
from .trial_forms import TrialRequestForm
from .trial_models import TrialRequest
from .trial_services import accessible_trials, duplicate_trial, trial_storage_ready


def rate_limited():
    response = HttpResponse("Забагато запитів. Спробуйте пізніше.", status=429)
    response["Retry-After"] = "900"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def trial_request(request, pk):
    course = get_object_or_404(Course, pk=pk, is_published=True)
    if not trial_storage_ready():
        return render(request, "enrollments/trial_unavailable.html", {"course": course}, status=503)
    form = TrialRequestForm(request.POST if request.method == "POST" else None, course=course, user=request.user,
                            initial={"requested_format": request.GET.get("format", "individual")})
    if request.method == "POST":
        if not allow_request("trial-ip", request.META.get("REMOTE_ADDR", ""), limit=5, seconds=900):
            return rate_limited()
        # A honeypot gets the same generic result, without saving contact data.
        if request.POST.get("website"):
            return redirect("trial_success")
        if form.is_valid():
            data = form.cleaned_data
            if not allow_request("trial-contact", normalize_email(data["email"]), limit=3, seconds=3600):
                return rate_limited()
            now = timezone.now()
            trial = TrialRequest(course=course, user=request.user if request.user.is_authenticated else None,
                student_name=data["student_name"], level=data["level"], is_minor=data["age_category"] == "minor",
                guardian_name=data["guardian_name"], email=data["email"], phone=data["phone"],
                requested_format=data["requested_format"], comment=data["comment"], consent_at=now,
                guardian_consent_at=now if data["age_category"] == "minor" else None)
            trial.update_identity()
            if not duplicate_trial(trial):
                try:
                    with transaction.atomic():
                        trial.save()
                except IntegrityError:
                    # Concurrent active duplicate: do not disclose the earlier request.
                    pass
                except ValidationError:
                    form.add_error(None, "Не вдалося зберегти заявку. Перевірте введені дані й повторіть спробу.")
                    return render(request, "enrollments/trial_form.html", {"course": course, "form": form})
            return redirect("trial_success")
    return render(request, "enrollments/trial_form.html", {"course": course, "form": form})


@never_cache
@require_safe
def trial_success(request):
    return render(request, "enrollments/trial_success.html")


@login_required
@never_cache
@require_safe
def trial_detail(request, pk):
    if not trial_storage_ready():
        return HttpResponse("Пробні заявки тимчасово недоступні.", status=503)
    trial = get_object_or_404(accessible_trials(request.user).select_related("course", "teacher"), pk=pk)
    return render(request, "enrollments/trial_detail.html", {"trial": trial, "is_teacher": request.user.role == "teacher" and trial.user_id != request.user.pk})
