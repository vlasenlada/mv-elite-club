
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from enrollments.models import Enrollment
from django.conf import settings

class Lesson(models.Model):

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Заплановане"
        COMPLETED = "completed", "Проведене"
        CANCELLED = "cancelled", "Скасоване"

    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="lessons",
        verbose_name="Запис учня на курс"
    )
    
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teaching_lessons",
        verbose_name="Викладач"
    )


    starts_at = models.DateTimeField(
        verbose_name="Дата та час початку"
    )

    ends_at = models.DateTimeField(
        verbose_name="Дата та час завершення"
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        verbose_name="Статус заняття"
    )

    meeting_url = models.URLField(
        blank=True,
        verbose_name="Посилання на онлайн-заняття"
    )

    class Meta:
        verbose_name = "Заняття"
        verbose_name_plural = "Заняття"
        ordering = ["starts_at"]

    def clean(self):
        super().clean()

        if (
            self.starts_at
            and self.ends_at
            and self.ends_at <= self.starts_at
        ):
            raise ValidationError({
                "ends_at": "Час завершення має бути пізніше початку."
            })

        if (
            self.enrollment_id
            and self.enrollment.status != Enrollment.Status.APPROVED
        ):
            # This rule also applies to forms that do not expose enrollment.
            raise ValidationError(
                "Заявка учня не підтверджена або скасована. "
                "Створення та редагування заняття недоступні.",
                code="enrollment_not_approved",
            )

        self.validate_schedule()
        self.validate_teacher_change()

    def validate_teacher_change(self):
        previous = None
        if self.pk:
            previous = Lesson.objects.filter(pk=self.pk).first()
        if previous and previous.teacher_id == self.teacher_id:
            return
        if not self.teacher_id and previous is None:
            return

        from users.teacher_assignment import get_assignment_teacher
        get_assignment_teacher(self.teacher_id)
        if previous:
            now = timezone.now()
            if (
                previous.status != self.Status.SCHEDULED
                or self.status != self.Status.SCHEDULED
                or previous.starts_at < now
                or self.starts_at < now
            ):
                raise ValidationError(
                    "Змінити викладача можна лише для майбутнього запланованого заняття. "
                    "Історію занять змінювати заборонено."
                )
            if self.assignments.exists():
                raise ValidationError(
                    "Неможливо змінити викладача: для цього заняття вже є домашні завдання."
                )

    def validate_schedule(self):
        if (
            self.status == self.Status.CANCELLED
            or not self.starts_at
            or not self.ends_at
            or self.ends_at <= self.starts_at
        ):
            return

        # Adjacent lessons are allowed: intervals are [starts_at, ends_at).
        overlapping = Lesson.objects.filter(
            starts_at__lt=self.ends_at,
            ends_at__gt=self.starts_at,
        ).exclude(status=self.Status.CANCELLED)
        if self.pk is not None:
            overlapping = overlapping.exclude(pk=self.pk)

        errors = []
        if self.teacher_id and overlapping.filter(teacher_id=self.teacher_id).exists():
            errors.append(ValidationError(
                "Викладач уже має інше заняття в цей час. Оберіть інший час або викладача.",
                code="teacher_schedule_conflict",
            ))
        if self.enrollment_id and overlapping.filter(
            enrollment__student_id=self.enrollment.student_id
        ).exists():
            errors.append(ValidationError(
                "Учень уже має інше заняття в цей час. Оберіть інший час.",
                code="student_schedule_conflict",
            ))
        if errors:
            # Non-field errors work in both partial forms and Django Admin.
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.enrollment.student.username} — "
            f"{self.enrollment.course.title}"
        )
