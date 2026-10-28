
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .forms import SubmissionForm
from .models import Assignment, Submission


@login_required
def submit_homework(request, assignment_id):

    # Домашні завдання можуть виконувати лише учні
    if (
        request.user.role != "student"
        or request.user.is_staff
    ):
        raise PermissionDenied

    # Знаходимо завдання саме цього учня
    assignment = get_object_or_404(
        Assignment.objects.select_related(
            "lesson__enrollment__course"
        ),
        pk=assignment_id,
        lesson__enrollment__student=request.user,
        lesson__enrollment__status="approved",
    )

    # Перевіряємо, чи учень уже надсилав відповідь
    submission = Submission.objects.filter(
        assignment=assignment,
        student=request.user
    ).first()

    # Оцінені роботи не дозволяємо редагувати
    if submission and submission.grade is not None:
        return redirect("student_dashboard")

    if request.method == "POST":

        form = SubmissionForm(
            request.POST,
            instance=submission
        )

        if form.is_valid():

            answer = form.save(commit=False)

            answer.assignment = assignment
            answer.student = request.user

            answer.save()

            return redirect("student_dashboard")

    else:
        form = SubmissionForm(instance=submission)

    return render(
        request,
        "learning/submit_homework.html",
        {
            "assignment": assignment,
            "form": form,
            "submission": submission,
        }
    )
