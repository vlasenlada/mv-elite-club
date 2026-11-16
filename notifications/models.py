from django.conf import settings
from django.db import models
from django.urls import reverse


class EventCursor(models.Model):
    key = models.CharField(max_length=100, primary_key=True)
    state = models.JSONField(default=dict)
    revision = models.PositiveBigIntegerField(default=0)


class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    event_key = models.CharField(max_length=180)
    kind = models.CharField(max_length=32)
    title = models.CharField(max_length=160)
    text = models.TextField()
    target_type = models.CharField(max_length=20)
    target_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["recipient", "event_key"], name="unique_recipient_notification_event")]
        indexes = [models.Index(fields=["recipient", "read_at", "-created_at"], name="notification_unread_idx")]
        verbose_name = "Сповіщення"
        verbose_name_plural = "Сповіщення"

    def destination(self):
        """Resolve only internal, currently authorized routes; never meeting URLs."""
        user = self.recipient
        teacher = user.role == "teacher"
        fallback = reverse("teacher_dashboard" if teacher else "student_dashboard")
        if self.target_type == "trial":
            from enrollments.trial_services import accessible_trials, trial_storage_ready
            if trial_storage_ready() and accessible_trials(user).filter(pk=self.target_id).exists():
                return reverse("trial_detail", args=[self.target_id])
            return fallback
        if self.target_type == "lesson":
            from scheduling.selectors import calendar_lessons
            if calendar_lessons(user).filter(pk=self.target_id).exists():
                return reverse("lesson_detail", args=[self.target_id])
            return reverse("lesson_calendar")
        if self.target_type == "assignment":
            from learning.models import Assignment
            assignment = Assignment.objects.select_related("lesson").filter(pk=self.target_id).first()
            if not teacher and assignment and assignment.lesson.has_student(user, current=True):
                return reverse("submit_homework", args=[assignment.pk])
            return fallback + ("" if teacher else "#my-homework")
        if self.target_type == "submission":
            return fallback + ("#teacher-reviews" if teacher else "#my-progress")
        if self.target_type == "group":
            from classrooms.models import StudyGroup
            group = StudyGroup.objects.filter(pk=self.target_id).first()
            if group:
                if teacher and (group.teacher_id == user.pk or group.lessons.filter(teacher=user).exists()):
                    return reverse("group_detail", args=[group.pk])
                if not teacher and group.memberships.filter(enrollment__student=user).exists():
                    return reverse("group_detail", args=[group.pk])
            return fallback
        return fallback + ("" if teacher else "#my-courses")
