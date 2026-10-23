
from django.core.exceptions import ValidationError
from django.db import models

from enrollments.models import Enrollment


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
            raise ValidationError({
                "enrollment": "Заявка учня має бути підтверджена."
            })

    def __str__(self):
        return (
            f"{self.enrollment.student.username} — "
            f"{self.enrollment.course.title}"
        )
