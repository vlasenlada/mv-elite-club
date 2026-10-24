
from django.contrib.auth import login
from django.shortcuts import render, redirect

from .forms import StudentRegistrationForm

from django.contrib.auth.decorators import login_required
from .models import User

from enrollments.models import Enrollment

from scheduling.models import Lesson

from django.utils import timezone

def register(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = StudentRegistrationForm(request.POST)

        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
    else:
        form = StudentRegistrationForm()

    return render(
        request,
        "users/register.html",
        {"form": form}
    )






@login_required
def student_dashboard(request):
    if (
        request.user.role != User.Role.STUDENT
        or request.user.is_staff
    ):
        return redirect("home")

    enrollments = Enrollment.objects.filter(
        student=request.user
    ).select_related("course")

    lessons = Lesson.objects.filter(
        enrollment__student=request.user
    ).select_related(
        "enrollment__course"
    ).order_by("starts_at")

    next_lesson = lessons.filter(
        starts_at__gte=timezone.now(),
        status="scheduled"
    ).first()

    return render(
        request,
        "users/dashboard.html",
        {
            "enrollments": enrollments,
            "lessons": lessons,
            "next_lesson": next_lesson,
        }
    )



    if (
        request.user.role != User.Role.STUDENT
        or request.user.is_staff
    ):
        return redirect("home")

    return render(
        request,
        "users/dashboard.html"
    )
