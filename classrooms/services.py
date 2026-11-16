from django.core.exceptions import ValidationError
from django.utils import timezone


def sync_attendance(lesson):
    from .models import Attendance
    if not lesson.group_id:
        return
    members = lesson.memberships_at_start().filter(enrollment__status="approved")
    Attendance.objects.bulk_create(
        [Attendance(lesson=lesson, membership=member) for member in members],
        ignore_conflicts=True,
    )


def plan_group_transfer(group):
    lessons = list(group.lessons.filter(status="scheduled", starts_at__gte=timezone.now()).exclude(teacher_id=group.teacher_id))
    if any(lesson.assignments.exists() for lesson in lessons):
        raise ValidationError("Передачу групи скасовано: майбутні заняття мають домашні завдання. Історію та призначення не змінено.")
    for lesson in lessons:
        lesson.group = group
        lesson.teacher = group.teacher
        lesson.full_clean()
    return lessons
