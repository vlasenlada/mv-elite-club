
from django.shortcuts import render, get_object_or_404
from django.db.models import Q

from .models import Course
from enrollments.models import Enrollment


def course_list(request):
    courses = Course.objects.filter(is_published=True)
    subject = request.GET.get("subject", "")
    grade = request.GET.get("grade", "")
    if subject == Course.Subject.NMT:
        courses = courses.filter(Q(subject=subject) | Q(grade_band=Course.GradeBand.NMT))
    elif subject in Course.Subject.values:
        courses = courses.filter(subject=subject)
    if grade in Course.GradeBand.values:
        courses = courses.filter(grade_band=grade)
    return render(request, "courses/list.html", {
        "courses": courses, "subjects": Course.Subject.choices,
        "grades": Course.GradeBand.choices, "selected_subject": subject, "selected_grade": grade,
    })


def course_detail(request, pk=None, slug=None):
    course = get_object_or_404(
        Course,
        is_published=True,
        **({"slug": slug} if slug else {"pk": pk}),
    )

    enrollment = None

    if request.user.is_authenticated:
        enrollment = Enrollment.objects.filter(
            student=request.user,
            course=course
        ).first()

    options = course.learning_options
    selected_format = request.GET.get("format", "")
    for option in options:
        option["selected"] = option["value"] == selected_format
        option["return_url"] = f"{course.get_absolute_url()}?format={option['value']}#learning-formats"
        for package in option["packages"]:
            package.return_url = f"{course.get_absolute_url()}?format={option['value']}&package={package.pk}#package-{package.pk}"
            package.selected = request.GET.get("package") == str(package.pk)

    return render(
        request,
        "courses/detail.html",
        {
            "course": course,
            "enrollment": enrollment,
            "learning_options": options,
        }
    )
