from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from learning.models import Assignment, Submission

from .forms import AttendanceForm, GroupLessonForm
from .models import Attendance, StudyGroup


@login_required
def group_detail(request, pk):
    group = get_object_or_404(StudyGroup.objects.select_related("course", "teacher"), pk=pk)
    is_teacher = request.user.role == "teacher" and not request.user.is_staff
    is_student = request.user.role == "student" and not request.user.is_staff
    lessons = group.lessons.select_related("group__course", "teacher").order_by("starts_at")
    if is_teacher:
        if group.teacher_id != request.user.pk and not lessons.filter(teacher=request.user).exists():
            raise Http404
        lessons = lessons.filter(teacher=request.user)
    elif is_student:
        if not group.memberships.filter(enrollment__student=request.user).exists():
            raise Http404
        lessons = lessons.for_student(request.user)
    else:
        return HttpResponseForbidden("Немає доступу до цієї групи.")

    form = None
    if is_teacher and group.teacher_id == request.user.pk and group.is_active:
        form = GroupLessonForm(
            request.POST if request.method == "POST" else None,
            teacher=request.user, initial={"group": group},
        )
        # A detail page must not create a lesson for a different group via POST.
        form.fields["group"].queryset = StudyGroup.objects.filter(pk=group.pk)
    if request.method == "POST":
        if form is None:
            return HttpResponseForbidden("Створювати заняття може лише призначений викладач активної групи.")
        if form.is_valid():
            form.save()
            messages.success(request, "Групове заняття створено для всіх учасників.")
            return redirect("group_detail", pk=group.pk)

    rows = []
    for lesson in lessons:
        attendance = lesson.attendances.filter(membership__in=lesson.memberships_at_start()).select_related("membership__enrollment__student")
        if is_student:
            attendance = attendance.filter(membership__enrollment__student=request.user)
        rows.append({"lesson": lesson, "attendance": attendance})
    assignments = Assignment.objects.filter(lesson__in=lessons).select_related("lesson")
    submissions = []
    if is_student:
        submissions = Submission.objects.with_participant_history().filter(
            assignment__lesson__group=group, student=request.user,
        ).select_related("assignment__lesson__group", "student")
    return render(request, "classrooms/detail.html", {
        "group": group, "rows": rows, "is_teacher": is_teacher,
        "lesson_form": form, "assignments": assignments,
        "submissions": submissions,
        "joinable_ids": set(lessons.for_student(request.user, current=True).values_list("pk", flat=True)) if is_student else set(),
        "answerable_ids": set(Assignment.objects.filter(lesson__in=lessons.for_student(request.user, current=True)).values_list("pk", flat=True)) if is_student else set(),
        "attendance_choices": Attendance.Status.choices,
        "members": group.memberships.filter(is_active=True, enrollment__status="approved").select_related("enrollment__student") if is_teacher and group.teacher_id == request.user.pk else [],
    })


@login_required
@require_POST
def update_attendance(request, pk):
    if request.user.role != "teacher" or request.user.is_staff:
        return HttpResponseForbidden("Відвідування відмічає викладач заняття.")
    attendance = get_object_or_404(
        Attendance.objects.select_related("lesson", "membership__enrollment__student"),
        pk=pk, lesson__teacher=request.user,
    )
    form = AttendanceForm(request.POST, instance=attendance)
    if form.is_valid():
        form.save()
        messages.success(request, "Відвідування збережено.")
    else:
        messages.error(request, "Не вдалося зберегти відвідування. Перевірте статус і склад групи на дату заняття.")
    return redirect("group_detail", pk=attendance.lesson.group_id)
