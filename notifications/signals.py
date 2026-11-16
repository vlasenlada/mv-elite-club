from django.db.models.signals import pre_save, post_save

from classrooms.models import GroupMembership, StudyGroup
from enrollments.models import Enrollment, TrialRequest
from learning.models import Assignment, Submission
from scheduling.models import Lesson

from .services import canonical, record_change, storage_ready


TRACKED = {
    TrialRequest: ("user_id", "course_id", "status", "teacher_id"),
    Enrollment: ("student_id", "course_id", "status", "teacher_id"),
    Lesson: ("enrollment_id", "group_id", "teacher_id", "starts_at", "ends_at", "status", "meeting_url"),
    Assignment: ("lesson_id", "title", "description", "due_at"),
    Submission: ("assignment_id", "student_id", "answer_text", "grade", "feedback"),
    StudyGroup: ("course_id", "teacher_id", "is_active"),
    GroupMembership: ("group_id", "enrollment_id", "is_active", "joined_at", "left_at"),
}


def before_save(sender, instance, raw=False, using="default", **kwargs):
    if not raw:
        row = sender._default_manager.using(using).filter(pk=instance.pk).values(*TRACKED[sender]).first() if instance.pk else None
        instance._notification_before = canonical(row)


def after_save(sender, instance, raw=False, using="default", **kwargs):
    if raw or not storage_ready(using):
        return
    row = sender._default_manager.using(using).filter(pk=instance.pk).values(*TRACKED[sender]).get()
    current = sender._default_manager.using(using).get(pk=instance.pk)
    record_change(current, getattr(instance, "_notification_before", {}), canonical(row), using)


for model in TRACKED:
    pre_save.connect(before_save, sender=model, dispatch_uid=f"notifications.before.{model._meta.label_lower}")
    post_save.connect(after_save, sender=model, dispatch_uid=f"notifications.after.{model._meta.label_lower}")
