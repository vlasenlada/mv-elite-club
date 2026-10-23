
from django.conf import settings
from django.db import models

from courses.models import Course


class Enrollment(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Очікує підтвердження"
        APPROVED = "approved", "Підтверджена"
        REJECTED = "rejected", "Відхилена"
        CANCELLED = "cancelled", "Скасована"

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Учень"
    )

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Курс"
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Статус заявки"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата подання заявки"
    )

    class Meta:
        verbose_name = "Заявка на навчання"
        verbose_name_plural = "Заявки на навчання"
        ordering = ["-created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["student", "course"],
                name="unique_student_course_enrollment"
            )
        ]

    def __str__(self):
        return f"{self.student.username} — {self.course.title}"
