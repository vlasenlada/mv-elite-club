
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse


class Course(models.Model):
    class CardTone(models.TextChoices):
        CREAM = "cream", "Кремовий"
        BLUE = "blue", "Голубий"
        WHEAT = "wheat", "Пшенично-бежевий"

    card_tone = models.CharField(max_length=12, choices=CardTone.choices, default=CardTone.CREAM, verbose_name="Колір картки")
    class GradeBand(models.TextChoices):
        PRIMARY = "primary", "1–4 класи"
        MIDDLE = "middle", "5–9 класи"
        NMT = "nmt", "10–11 класи · НМТ"

    slug = models.SlugField(max_length=180, unique=True, null=True, blank=True)
    grade_band = models.CharField(max_length=20, choices=GradeBand.choices, blank=True, verbose_name="Класи")
    tagline = models.CharField(max_length=240, blank=True, verbose_name="Короткий опис")
    program = models.TextField(blank=True, verbose_name="Програма", help_text="Один пункт на рядок.")
    benefits = models.TextField(blank=True, verbose_name="Переваги", help_text="Один пункт на рядок.")
    outcomes = models.TextField(blank=True, verbose_name="Результати навчання", help_text="Один пункт на рядок.")
    supports_individual = models.BooleanField(default=True, verbose_name="Індивідуальне навчання")
    supports_group = models.BooleanField(default=True, verbose_name="Групове навчання")
    individual_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Індивідуальне заняття, грн",
        help_text="Вартість одного заняття один на один. Порожньо — вартість уточнюється.",
    )
    group_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name="Групове заняття за одного учня, грн",
        help_text="Вартість одного групового заняття для одного учня. Не вартість пакета чи всієї групи.",
    )

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
        null=True,
        blank=True,
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

    def clean(self):
        super().clean()
        self.slug = self.slug or None
        if not self.supports_individual and not self.supports_group:
            from django.core.exceptions import ValidationError
            raise ValidationError("Оберіть щонайменше один формат навчання.")
        if self.pk and not self.supports_group and self.study_groups.filter(is_active=True).exists():
            from django.core.exceptions import ValidationError
            raise ValidationError("Перед вимкненням групового формату архівуйте активні групи курсу.")

    def get_absolute_url(self):
        if self.slug:
            return reverse("course_detail_slug", args=[self.slug])
        return reverse("course_detail", args=[self.pk])

    @property
    def format_label(self):
        if self.supports_individual and self.supports_group:
            return "Індивідуально та в групі"
        return "Індивідуально" if self.supports_individual else "У групі"

    def price_for_format(self, lesson_format):
        return {self.LessonFormat.INDIVIDUAL: self.individual_price,
                self.LessonFormat.GROUP: self.group_price}.get(lesson_format)

    @property
    def learning_options(self):
        options = []
        if self.supports_individual:
            options.append({
                "value": "individual", "title": "Індивідуальне навчання", "icon": "person",
                "price": self.individual_price, "unit": "за одне заняття", "button": "Обрати індивідуальне навчання",
                "features": ["Один на один із викладачем", "Індивідуальний темп і підхід", "Розклад з урахуванням твоїх цілей"],
            })
        if self.supports_group:
            options.append({
                "value": "group", "title": "Групове навчання", "icon": "users",
                "price": self.group_price, "unit": "за одне заняття для одного учня", "button": "Обрати групове навчання",
                "features": ["Спільні заняття з викладачем", "Загальний розклад і групова практика", "Власні відповіді, оцінки та зворотний зв’язок"],
            })
        return options

    @property
    def program_items(self):
        return [line.strip() for line in self.program.splitlines() if line.strip()]

    @property
    def benefit_items(self):
        return [line.strip() for line in self.benefits.splitlines() if line.strip()]

    @property
    def outcome_items(self):
        return [line.strip() for line in self.outcomes.splitlines() if line.strip()]
