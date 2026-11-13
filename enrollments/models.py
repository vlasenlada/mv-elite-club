
from django.conf import settings
from django.core.exceptions import ValidationError
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

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_enrollments",
        limit_choices_to={"role": "teacher"},
        verbose_name="Призначений викладач"
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

    def clean(self):
        super().clean()
        previous = None
        if self.pk:
            previous = Enrollment.objects.filter(pk=self.pk).first()
        if previous and previous.teacher_id == self.teacher_id:
            return
        if not self.teacher_id and previous is None:
            return

        from users.teacher_assignment import get_assignment_teacher
        get_assignment_teacher(self.teacher_id)
        if self.status != self.Status.APPROVED:
            raise ValidationError("Призначення викладача можливе лише для підтвердженої заявки.")
        if previous:
            if (self.student_id, self.course_id) != (previous.student_id, previous.course_id):
                raise ValidationError(
                    "Передача викладачу не може одночасно змінювати учня або курс."
                )
            from .services import plan_teacher_transfer
            plan_teacher_transfer(self)

    def __str__(self):
        return f"{self.student.username} — {self.course.title}"
