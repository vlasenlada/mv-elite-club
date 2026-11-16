
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse


def lesson_count_label(count):
    return f"{count} заняття" if count == 4 else f"{count} занять"


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
        for option in options:
            packages = list(self.packages.filter(learning_format=option["value"], is_active=True).order_by("lesson_count"))
            previous = None
            for package in packages:
                package.saving_amount = None
                package.compared_to = None
                package.compared_to_label = None
                if previous and package.price is not None and previous.price is not None:
                    saving = previous.price * Decimal(package.lesson_count) / Decimal(previous.lesson_count) - package.price
                    saving = saving.quantize(Decimal("0.01"))
                    if saving > 0:
                        package.saving_amount = saving
                        package.compared_to = previous.lesson_count
                        package.compared_to_label = lesson_count_label(previous.lesson_count)
                if package.price is not None:
                    previous = package
            option["packages"] = packages
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


class CoursePackage(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="packages", verbose_name="Курс")
    learning_format = models.CharField(max_length=20, choices=Course.LessonFormat.choices, verbose_name="Формат")
    lesson_count = models.PositiveSmallIntegerField(choices=[(n, str(n)) for n in (4, 8, 12)], verbose_name="Кількість занять")
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))], verbose_name="Загальна вартість пакета, грн",
        help_text="Для групи — за одного учня. Порожньо — вартість уточнюється.")
    is_active = models.BooleanField(default=True, verbose_name="Доступний для вибору")

    class Meta:
        verbose_name = "Тариф пакета"
        verbose_name_plural = "Тарифи пакетів"
        ordering = ["course", "learning_format", "lesson_count"]
        constraints = [models.UniqueConstraint(fields=["course", "learning_format", "lesson_count"], name="unique_course_format_package"),
            models.CheckConstraint(condition=models.Q(lesson_count__in=[4, 8, 12]), name="package_size_4_8_12"),
            models.CheckConstraint(condition=models.Q(price__isnull=True) | models.Q(price__gte=0), name="nonnegative_package_price")]

    def __str__(self):
        return f"{self.course.title} · {self.get_learning_format_display()} · {self.lesson_label}"

    @property
    def lesson_label(self):
        return lesson_count_label(self.lesson_count)

    @property
    def unit_price(self):
        return None if self.price is None else (self.price / Decimal(self.lesson_count)).quantize(Decimal("0.01"))

    def clean(self):
        super().clean()
        if self.pk:
            previous = CoursePackage.objects.get(pk=self.pk)
            if (previous.course_id, previous.learning_format, previous.lesson_count) != (self.course_id, self.learning_format, self.lesson_count) and self.enrollments.exists():
                from django.core.exceptions import ValidationError
                raise ValidationError("Не змінюйте курс, формат або обсяг уже вибраного пакета. Створіть інший тариф.")
