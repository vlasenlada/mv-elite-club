
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

    enrollment, created = Enrollment.objects.get_or_create(
        student=request.user,
        course=course,
        defaults={
            "status": Enrollment.Status.PENDING
        }
    )

    if created:
        messages.success(
            request,
            "Заявку успішно надіслано!"
        )
    else:
        messages.info(
            request,
            "Ти вже маєш заявку на цей курс."
        )

    return redirect("course_detail", pk=pk)
