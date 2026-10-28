
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Assignment(models.Model):
    lesson = models.ForeignKey(
        "scheduling.Lesson",
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="Заняття"
    )

    title = models.CharField(
        max_length=200,
        verbose_name="Назва завдання"
    )

    description = models.TextField(
        verbose_name="Опис завдання"
    )

    due_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Термін здачі"
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Домашнє завдання"
        verbose_name_plural = "Домашні завдання"


class Submission(models.Model):
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="Домашнє завдання"
    )

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="homework_submissions",
        verbose_name="Учень"
    )

    answer_text = models.TextField(
        verbose_name="Відповідь учня"
    )

    submitted_at = models.DateTimeField(
        auto_now_add=True
    )

    grade = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100),
        ],
        verbose_name="Оцінка (0–100)"
    )

    feedback = models.TextField(
        blank=True,
        verbose_name="Коментар викладача"
    )

    def __str__(self):
        return f"{self.student} — {self.assignment.title}"

    class Meta:
        verbose_name = "Виконана робота"
        verbose_name_plural = "Виконані роботи"
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "student"],
                name="unique_student_assignment"
            )
        ]
