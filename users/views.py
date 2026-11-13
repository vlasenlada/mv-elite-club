
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect

from .forms import StudentRegistrationForm, TeacherChoiceForm

from django.contrib.auth.decorators import login_required
from .models import User

from enrollments.models import Enrollment
from enrollments.services import replace_lesson_teacher, transfer_enrollment_teacher

from scheduling.models import Lesson



from scheduling.forms import (
    TeacherLessonForm,
    TeacherMeetingLinkForm,
    TeacherLessonEditForm,
)




from django.utils import timezone

from learning.models import Assignment, Submission


from django.db.models import F
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from learning.forms import TeacherReviewForm, TeacherAssignmentForm

from django.contrib import messages





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
        status="scheduled",
        enrollment__status=Enrollment.Status.APPROVED,
    ).first()


    assignments = Assignment.objects.filter(
        lesson__enrollment__student=request.user
    ).select_related(
        "lesson__enrollment__course"
    ).order_by("due_at", "-created_at")


    graded_submissions = Submission.objects.filter(
        student=request.user,
        grade__isnull=False
    ).select_related(
        "assignment__lesson__enrollment__course"
    ).order_by("-submitted_at")


    graded_assignment_ids = set(
        graded_submissions.values_list("assignment_id", flat=True)
    )

    # Усі завдання, на які учень уже надіслав відповідь
    submitted_assignment_ids = set(
        Submission.objects.filter(
            student=request.user,
            assignment__lesson__enrollment__student=request.user
        ).values_list("assignment_id", flat=True)
    )


    return render(
        request,
        "users/dashboard.html",
        {
            "enrollments": enrollments,
            "lessons": lessons,
            "next_lesson": next_lesson,
            "assignments": assignments,
            "graded_submissions": graded_submissions,
            "graded_assignment_ids": graded_assignment_ids,
            "submitted_assignment_ids": submitted_assignment_ids,

        }
    )




@login_required
@never_cache
@require_safe
def join_lesson(request, lesson_id):
    if (
        request.user.role != User.Role.STUDENT
        or request.user.is_staff
    ):
        return HttpResponseForbidden("Підключення до заняття доступне лише учням.")

    lesson = get_object_or_404(
        Lesson.objects.exclude(meeting_url=""),
        pk=lesson_id,
        enrollment__student=request.user,
        enrollment__status=Enrollment.Status.APPROVED,
        status=Lesson.Status.SCHEDULED,
    )
    return redirect(lesson.meeting_url)


@login_required
def teacher_dashboard(request):
    if (
        request.user.role != User.Role.TEACHER
        or request.user.is_staff
    ):
        return redirect("home")


 
    # Визначаємо, яку форму надіслав викладач
    is_lesson_post = (
        request.method == "POST"
        and request.POST.get("form_type") == "lesson"
    )

    # Форма домашнього завдання
    assignment_form = TeacherAssignmentForm(
        request.POST
        if request.method == "POST" and not is_lesson_post
        else None,
        teacher=request.user
    )

    # Форма нового заняття
    lesson_form = TeacherLessonForm(
        request.POST if is_lesson_post else None,
        teacher=request.user
    )

    if request.method == "POST":

        # Створення заняття
      
        if is_lesson_post:
            if lesson_form.is_valid():
                lesson = lesson_form.save(commit=False)

                # Хто призначений викладачем цього учня
                assigned_teacher_id = lesson.enrollment.teacher_id

                if assigned_teacher_id != request.user.id:
                    messages.error(
                        request,
                        "Ви не призначені викладачем цього учня."
                    )
                    return redirect("teacher_dashboard")

                # Зберігаємо заняття
                lesson.teacher = request.user
                lesson.save()

                messages.success(
                    request,
                    "Заняття успішно створено!"
                )

                return redirect("teacher_dashboard")


        # Створення домашнього завдання
        elif assignment_form.is_valid():
            assignment_form.save()

            messages.success(
                request,
                "Домашнє завдання успішно створено!"
            )

            return redirect("teacher_dashboard")



    # Завдання, створені для занять цього викладача
    teacher_assignments = (
        Assignment.objects.filter(
            lesson__teacher=request.user
        )
        .select_related(
            "lesson__enrollment__student",
            "lesson__enrollment__course"
        )
        .order_by("-created_at")
    )

    # Заняття тільки цього викладача
    teacher_lessons = list(
        Lesson.objects.filter(
            teacher=request.user
        ).select_related(
            "enrollment__student",
            "enrollment__course"
        ).order_by("starts_at")
    )

   
    # Учні, яких адміністрація закріпила за викладачем
    assigned_enrollments = Enrollment.objects.filter(
        teacher=request.user,
        status=Enrollment.Status.APPROVED
    ).select_related("student", "course")

    student_entries = []
    seen = set()

    for enrollment in assigned_enrollments:
        key = (enrollment.student_id, enrollment.course_id)

        if key not in seen:
            seen.add(key)
            student_entries.append({
                "student": enrollment.student,
                "course": enrollment.course,
            })


    # Розділяємо майбутні та минулі заняття
    now = timezone.now()

    upcoming_lessons = [
        lesson for lesson in teacher_lessons
        if lesson.starts_at >= now
    ]


    past_lessons = [
        lesson for lesson in teacher_lessons
        if lesson.starts_at < now
    ]

    # Домашні роботи учнів цього викладача
    teacher_submissions = (
        Submission.objects.filter(
            assignment__lesson__teacher=request.user,
            student=F(
                "assignment__lesson__enrollment__student"
            )
        )
        .select_related(
            "student",
            "assignment__lesson__enrollment__course"
        )
        .order_by("-submitted_at")
    )

    return render(
        request,
        "users/teacher_dashboard.html",
        {
            "student_entries": student_entries,
            "upcoming_lessons": upcoming_lessons,
            "past_lessons": past_lessons,
            "teacher_submissions": teacher_submissions,
            "assignment_form": assignment_form,
            "teacher_assignments": teacher_assignments,
            "lesson_form": lesson_form,

        }
    )


@login_required
@require_POST
def review_submission(request, submission_id):
    if (
        request.user.role != User.Role.TEACHER
        or request.user.is_staff
    ):
        return HttpResponseForbidden(
            "У вас немає доступу до цієї роботи."
        )

    submission = get_object_or_404(
        Submission,
        pk=submission_id,
        assignment__lesson__teacher=request.user,
        student=F(
            "assignment__lesson__enrollment__student"
        )
    )

    form = TeacherReviewForm(request.POST)

    if form.is_valid():
        submission.grade = form.cleaned_data["grade"]
        submission.feedback = form.cleaned_data["feedback"]

        submission.save(
            update_fields=["grade", "feedback"]
        )

    return redirect("teacher_dashboard")


@login_required
@require_POST
def update_lesson_status(request, lesson_id):

    # Перевіряємо, що користувач — викладач
    if (
        request.user.role != User.Role.TEACHER
        or request.user.is_staff
    ):
        return HttpResponseForbidden(
            "У вас немає доступу."
        )

    # Знаходимо заняття тільки цього викладача
    lesson = get_object_or_404(
        Lesson,
        id=lesson_id,
        teacher=request.user
    )

    new_status = request.POST.get("status")

    # Дозволяємо лише визначені статуси
    allowed_statuses = [
        Lesson.Status.SCHEDULED,
        Lesson.Status.COMPLETED,
        Lesson.Status.CANCELLED,
    ]

    if new_status not in allowed_statuses:
        return HttpResponseForbidden(
            "Недопустимий статус заняття."
        )

    lesson.status = new_status
    try:
        lesson.validate_schedule()
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
        return redirect("teacher_dashboard")
    lesson.save(update_fields=["status"])

    messages.success(
        request,
        "Статус заняття успішно оновлено!"
    )

    return redirect("teacher_dashboard")



@login_required
@require_POST
def update_meeting_link(request, lesson_id):

    if (
        request.user.role != User.Role.TEACHER
        or request.user.is_staff
    ):
        return HttpResponseForbidden(
            "У вас немає доступу."
        )

    lesson = get_object_or_404(
        Lesson,
        id=lesson_id,
        teacher=request.user
    )

    form = TeacherMeetingLinkForm(
        request.POST,
        instance=lesson
    )

    if form.is_valid():
        form.save()

        messages.success(
            request,
            "Посилання на заняття успішно збережено!"
        )
    else:
        if form.non_field_errors():
            for error in form.non_field_errors():
                messages.error(request, error)
        else:
            messages.error(
                request,
                "Не вдалося зберегти посилання. Перевірте URL."
            )

    return redirect("teacher_dashboard")



@login_required
def edit_teacher_lesson(request, lesson_id):

    # Доступ тільки для викладача
    if (
        request.user.role != User.Role.TEACHER
        or request.user.is_staff
    ):
        return HttpResponseForbidden(
            "У вас немає доступу."
        )

    # Шукаємо тільки заняття цього викладача
    lesson = get_object_or_404(
        Lesson,
        id=lesson_id,
        teacher=request.user
    )

    # Форма редагування
    if request.method == "POST":
        form = TeacherLessonEditForm(
            request.POST,
            instance=lesson
        )

        if form.is_valid():
            form.save()

            messages.success(
                request,
                "Зміни заняття успішно збережено!"
            )

            return redirect("teacher_dashboard")

    else:
        form = TeacherLessonEditForm(
            instance=lesson
        )

    return render(
        request,
        "users/edit_teacher_lesson.html",
        {
            "form": form,
            "lesson": lesson,
        }
    )



@login_required
def school_admin_dashboard(request):

    # Доступ тільки адміністраторам школи
    if not (
        request.user.is_staff
        and (
            request.user.role == User.Role.ADMIN
            or request.user.is_superuser
        )
    ):
        return HttpResponseForbidden(
            "У вас немає доступу до панелі адміністратора."
        )

    # Усі заявки на курси
    enrollments = (
        Enrollment.objects
        .select_related("student", "course")
        .order_by("-created_at")
    )

    # Зареєстровані учні
    students = User.objects.filter(
        role=User.Role.STUDENT
    ).order_by("username")

    # Зареєстровані викладачі
    teachers = User.objects.filter(
        role=User.Role.TEACHER
    ).order_by("username")

    # Усі заняття школи
    lessons = (
        Lesson.objects
        .select_related(
            "teacher",
            "enrollment__student",
            "enrollment__course"
        )
        .order_by("-starts_at")
    )

    
    context = {
        "enrollments": enrollments,
        "students": students,
        "teachers": teachers,
        "lessons": lessons,
        "enrollment_status_choices": Enrollment.Status.choices,
    }

    return render(
        request,
        "users/school_admin_dashboard.html",
        context
    )



@login_required
@require_POST
def update_enrollment_status(request, enrollment_id):

    # Доступ тільки для адміністрації школи
    if not (
        request.user.is_staff
        and (
            request.user.role == User.Role.ADMIN
            or request.user.is_superuser
        )
    ):
        return HttpResponseForbidden(
            "У вас немає доступу."
        )

    enrollment = get_object_or_404(
        Enrollment,
        id=enrollment_id
    )

    new_status = request.POST.get("status")

    # Перевірка допустимих статусів
    if new_status not in Enrollment.Status.values:
        messages.error(
            request,
            "Неприпустимий статус заявки."
        )
        return redirect("school_admin_dashboard")

    enrollment.status = new_status
    enrollment.save(update_fields=["status"])

    messages.success(
        request,
        "Статус заявки успішно оновлено!"
    )

    return redirect("school_admin_dashboard")



@login_required
@require_POST
def assign_lesson_teacher(request, lesson_id):

    # Перевірка прав адміністратора
    if not (
        request.user.is_staff
        and (
            request.user.role == User.Role.ADMIN
            or request.user.is_superuser
        )
    ):
        return HttpResponseForbidden(
            "У вас немає доступу."
        )

    # Тільки заплановані заняття
    lesson = get_object_or_404(
        Lesson,
        id=lesson_id,
        status=Lesson.Status.SCHEDULED
    )

    form = TeacherChoiceForm(request.POST)
    if not form.is_valid():
        for error in form.errors["teacher_id"]:
            messages.error(request, error)
        return redirect("school_admin_dashboard")
    teacher = form.cleaned_data["teacher_id"]

    # Якщо викладач уже призначений
    if lesson.teacher_id == teacher.id:
        messages.info(
            request,
            "Цей викладач уже призначений на заняття."
        )
        return redirect("school_admin_dashboard")

    try:
        replace_lesson_teacher(lesson.pk, teacher.pk)
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
        return redirect("school_admin_dashboard")

    messages.success(
        request,
        "Викладача успішно призначено!"
    )

    return redirect("school_admin_dashboard")



@login_required
@require_POST
def assign_enrollment_teacher(request, enrollment_id):

    # Доступ тільки для адміністратора
    if not (
        request.user.is_staff
        and (
            request.user.role == User.Role.ADMIN
            or request.user.is_superuser
        )
    ):
        return HttpResponseForbidden(
            "У вас немає доступу."
        )

    # Знаходимо підтверджену заявку
    enrollment = get_object_or_404(
        Enrollment,
        id=enrollment_id,
        status=Enrollment.Status.APPROVED
    )

    form = TeacherChoiceForm(request.POST)
    if not form.is_valid():
        for error in form.errors["teacher_id"]:
            messages.error(request, error)
        return redirect("school_admin_dashboard")
    teacher = form.cleaned_data["teacher_id"]

    # Якщо викладач уже призначений
    if enrollment.teacher_id == teacher.id:
        messages.info(
            request,
            "Цей викладач уже закріплений за курсом."
        )
        return redirect("school_admin_dashboard")

    try:
        moved_count = transfer_enrollment_teacher(enrollment.pk, teacher.pk)
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
        return redirect("school_admin_dashboard")

    messages.success(
        request,
        f"Викладача успішно призначено на курс. "
        f"Передано майбутніх занять: {moved_count}. Історію занять збережено."
    )

    return redirect("school_admin_dashboard")
