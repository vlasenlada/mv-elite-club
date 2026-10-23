
from django.shortcuts import render, get_object_or_404

from .models import Course
from enrollments.models import Enrollment


def course_detail(request, pk):
    course = get_object_or_404(
        Course,
        pk=pk,
        is_published=True
    )

    enrollment = None

    if request.user.is_authenticated:
        enrollment = Enrollment.objects.filter(
            student=request.user,
            course=course
        ).first()

    return render(
        request,
        "courses/detail.html",
        {
            "course": course,
            "enrollment": enrollment,
        }
    )
