
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from courses.models import Course
from users.models import User
from .models import Enrollment


@login_required
@require_POST
def enroll_course(request, pk):

    if (
        request.user.role != User.Role.STUDENT
        or request.user.is_staff
    ):
        messages.error(
            request,
            "Тільки учні можуть подавати заявки."
        )
        return redirect("course_detail", pk=pk)

    course = get_object_or_404(
        Course,
        pk=pk,
        is_published=True
    )

    requested_format = request.POST.get("requested_format", Course.LessonFormat.INDIVIDUAL)
    supported = {
        Course.LessonFormat.INDIVIDUAL: course.supports_individual,
        Course.LessonFormat.GROUP: course.supports_group,
    }
    if not supported.get(requested_format, False):
        messages.error(request, "Оберіть доступний формат навчання.")
        return redirect(course.get_absolute_url())

    enrollment, created = Enrollment.objects.get_or_create(
        student=request.user,
        course=course,
        defaults={
            "status": Enrollment.Status.PENDING,
            "requested_format": requested_format,
            "format_is_explicit": True,
        }
    )

    if created:
        messages.success(
            request,
            "Заявку успішно надіслано!"
        )
    else:
        messages.info(request, f"Заявка на цей курс уже існує. Обраний формат: {enrollment.get_requested_format_display()}. "
                      "Нову заявку не створено; для зміни формату звернися до адміністрації.")

    return redirect("course_detail", pk=pk)
