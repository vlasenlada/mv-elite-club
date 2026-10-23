
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Course(models.Model):

    class Subject(models.TextChoices):
        ENGLISH = "english", "Англійська мова"
        MATH = "math", "Математика"
        NMT = "nmt", "Підготовка до НМТ"

    class LessonFormat(models.TextChoices):
        INDIVIDUAL = "individual", "Індивідуальні заняття"
        GROUP = "group", "Групові заняття"

    title = models.CharField(
        max_length=200,
        verbose_name="Назва курсу"
    )

    subject = models.CharField(
        max_length=20,
        choices=Subject.choices,
        verbose_name="Предмет"
    )

    description = models.TextField(
        verbose_name="Опис курсу"
    )

    lesson_format = models.CharField(
        max_length=20,
        choices=LessonFormat.choices,
        verbose_name="Формат занять"
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.00"))
        ],
        verbose_name="Ціна за одне заняття (грн)"
    )

    is_published = models.BooleanField(
        default=False,
        verbose_name="Опублікований"
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата створення"
    )

    class Meta:
        verbose_name = "Курс"
        verbose_name_plural = "Курси"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
