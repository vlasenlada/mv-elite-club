from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError


TEACHER_ERROR = "Оберіть активного викладача без адміністративних прав."


def eligible_teachers():
    return get_user_model().objects.filter(
        role="teacher", is_active=True, is_staff=False, is_superuser=False
    )


def get_assignment_teacher(teacher_id, *, lock=False):
    teachers = eligible_teachers()
    if lock:
        teachers = teachers.select_for_update()
    try:
        return teachers.get(pk=teacher_id)
    except (get_user_model().DoesNotExist, TypeError, ValueError, OverflowError):
        raise ValidationError(TEACHER_ERROR, code="invalid_teacher") from None
