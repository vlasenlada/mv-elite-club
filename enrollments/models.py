
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from courses.models import Course


class Enrollment(models.Model):
    format_is_explicit = models.BooleanField(default=False, editable=False)
    requested_format = models.CharField(
        max_length=20, choices=Course.LessonFormat.choices,
        default=Course.LessonFormat.INDIVIDUAL, verbose_name="Бажаний формат",
    )

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
        if (previous and (previous.student_id, previous.course_id) != (self.student_id, self.course_id)
                and (previous.lessons.exists() or previous.group_memberships.exists())):
            raise ValidationError("Не змінюйте учня або курс заявки, пов'язаної з навчальною історією.")
        if previous and previous.requested_format != self.requested_format and (previous.lessons.exists() or previous.group_memberships.exists()):
            raise ValidationError("Не змінюйте формат заявки, яка вже має заняття або історію участі в групі.")
        if self.format_is_explicit and self.course_id and (previous is None or previous.requested_format != self.requested_format):
            allowed = {"individual": self.course.supports_individual, "group": self.course.supports_group}
            if not allowed.get(self.requested_format, False):
                raise ValidationError("Обраний формат недоступний для цього курсу.")
        if (self.format_is_explicit and self.requested_format == "group" and self.teacher_id
                and (previous is None or previous.teacher_id != self.teacher_id
                     or previous.requested_format != self.requested_format)):
            raise ValidationError("Для групової заявки призначте викладача навчальній групі, а не індивідуально учню.")
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

    @transaction.atomic
    def save(self, *args, **kwargs):
        previous = Enrollment.objects.filter(pk=self.pk).first() if self.pk else None
        super().save(*args, **kwargs)
        if previous and previous.status == self.Status.APPROVED and self.status != self.Status.APPROVED:
            for member in self.group_memberships.filter(is_active=True):
                member.is_active = False
                member.save(update_fields=["is_active"])

    def __str__(self):
        suffix = " · історична заявка" if not self.format_is_explicit else ""
        return f"{self.student.username} — {self.course.title} · {self.get_requested_format_display()}{suffix}"
