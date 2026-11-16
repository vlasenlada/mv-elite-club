from .models import Lesson


def calendar_lessons(user):
    """Reuse approved access, membership dates and teacher assignment."""
    lessons = Lesson.objects.select_related("enrollment__course", "group__course", "teacher")
    if user.is_staff or not user.is_active:
        return lessons.none()
    if user.role == "teacher":
        return lessons.filter(teacher=user)
    if user.role == "student":
        return lessons.for_student(user, current=True)
    return lessons.none()
