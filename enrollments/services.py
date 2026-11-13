from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from users.teacher_assignment import get_assignment_teacher

from .models import Enrollment


def plan_teacher_transfer(enrollment, *, lock=False):
    """Validate the complete transfer before changing any persisted record."""
    from learning.models import Assignment
    from scheduling.models import Lesson

    teacher = get_assignment_teacher(enrollment.teacher_id, lock=lock)
    if enrollment.status != Enrollment.Status.APPROVED:
        raise ValidationError("Призначення викладача можливе лише для підтвердженої заявки.")

    lessons = Lesson.objects.filter(
        enrollment_id=enrollment.pk,
        status=Lesson.Status.SCHEDULED,
        starts_at__gte=timezone.now(),
    ).exclude(teacher_id=teacher.pk).order_by("starts_at", "pk")
    if lock:
        lessons = lessons.select_for_update()
    lessons = list(lessons)
    if Assignment.objects.filter(lesson__in=lessons).exists():
        raise ValidationError(
            "Передачу скасовано: для майбутніх занять уже є домашні завдання. "
            "Автоматичне перенесення таких занять заборонено. "
            "Призначення на курс і заняття залишено без змін."
        )
    for lesson in lessons:
        # Admin can approve the enrollment in the same unsaved form.
        lesson.enrollment = enrollment
        lesson.teacher = teacher
        lesson.full_clean()
    return lessons


@transaction.atomic
def transfer_enrollment_teacher(enrollment_id, teacher_id):
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment_id)
    teacher = get_assignment_teacher(teacher_id, lock=True)
    if enrollment.status != Enrollment.Status.APPROVED:
        raise ValidationError("Призначення викладача можливе лише для підтвердженої заявки.")
    if enrollment.teacher_id == teacher.pk:
        return 0

    enrollment.teacher = teacher
    lessons = plan_teacher_transfer(enrollment, lock=True)
    for lesson in lessons:
        lesson.save(update_fields=["teacher"])
    enrollment.save(update_fields=["teacher"])
    return len(lessons)


@transaction.atomic
def replace_lesson_teacher(lesson_id, teacher_id):
    from scheduling.models import Lesson

    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    teacher = get_assignment_teacher(teacher_id, lock=True)
    if lesson.teacher_id == teacher.pk:
        return
    lesson.teacher = teacher
    lesson.full_clean()
    lesson.save(update_fields=["teacher"])
