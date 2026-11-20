import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.urls import reverse


class Assessment(models.Model):
    class Subject(models.TextChoices):
        ENGLISH = "english", "Англійська мова"
        MATH = "math", "Математика"

    class Category(models.TextChoices):
        GENERAL = "general", "Загальні навички"
        GRAMMAR = "grammar", "Граматика"
        VOCABULARY = "vocabulary", "Словниковий запас"
        READING = "reading", "Розуміння прочитаного"

    title = models.CharField(max_length=180, verbose_name="Назва тесту")
    slug = models.SlugField(max_length=100, verbose_name="Адреса")
    description = models.TextField(max_length=5000, blank=True, verbose_name="Опис")
    subject = models.CharField(max_length=10, choices=Subject.choices, verbose_name="Предмет")
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.GENERAL, verbose_name="Напрям перевірки")
    grade_band = models.CharField(max_length=12, blank=True, choices=[("primary", "1–4 класи"), ("middle", "5–9 класи"), ("nmt", "Підготовка до НМТ")], verbose_name="Напрям / класи")
    grade = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(11)], verbose_name="Конкретний клас")
    audience = models.CharField(max_length=120, blank=True, verbose_name="Кому підходить / навички")
    question_count = models.PositiveSmallIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(100)], verbose_name="Кількість завдань у спробі", help_text="Перші активні завдання за встановленим порядком.")
    estimated_minutes = models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(180)], verbose_name="Приблизна тривалість, хв")
    is_published = models.BooleanField(default=False, verbose_name="Опубліковано")
    level_estimation_enabled = models.BooleanField(default=False, verbose_name="Методично перевірена орієнтовна оцінка мовного рівня")
    methodology_notes = models.TextField(blank=True, max_length=5000, verbose_name="Обґрунтування методики", help_text="Хто й як перевірив завдання та пороги. Сам прапорець не є доказом валідності.")
    source_notes = models.TextField(blank=True, max_length=3000, verbose_name="Авторство / права на матеріали")
    seo_title = models.CharField(max_length=180, blank=True, verbose_name="SEO-заголовок")
    seo_description = models.CharField(max_length=320, blank=True, verbose_name="Meta description")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Тест"
        verbose_name_plural = "Тести"
        ordering = ["subject", "grade_band", "grade", "title", "pk"]
        constraints = [models.UniqueConstraint(fields=["subject", "slug"], name="unique_knowledge_test_url")]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("knowledge:detail", args=[self.subject, self.slug])

    @property
    def effective_band(self):
        if self.grade:
            return "primary" if self.grade <= 4 else "middle" if self.grade <= 9 else "nmt"
        return self.grade_band

    def validate_content(self):
        questions = list(self.questions.filter(is_active=True).prefetch_related("options").select_related("passage").order_by("order", "pk")) if self.pk else []
        if len(questions) < self.question_count:
            raise ValidationError("Недостатньо активних завдань. Спершу збережіть чернетку та додайте завдання.")
        for question in questions:
            question.validate_content()
        bands = list(self.result_bands.order_by("minimum_percent"))
        if bands and bands[0].minimum_percent != 0:
            raise ValidationError("Перший діапазон результатів має починатися з 0%.")
        if self.level_estimation_enabled and (not bands or any(not band.level_hint for band in bands)):
            raise ValidationError("Для орієнтовної мовної оцінки заповніть рівні в усіх діапазонах результатів.")
        for rule in self.recommendations.select_related("course", "assessment"):
            rule.assessment = self
            rule.full_clean()

    def clean(self):
        super().clean()
        if self.slug in {"nmt", "sitemap", "attempt"}:
            raise ValidationError({"slug": "Ця адреса зарезервована. Оберіть іншу."})
        if self.grade and self.grade_band and self.effective_band != self.grade_band:
            raise ValidationError("Клас має відповідати напряму навчання.")
        if self.level_estimation_enabled and (self.subject != "english" or not self.methodology_notes.strip()):
            raise ValidationError("Для мовної оцінки потрібні англійський тест і документоване методичне обґрунтування.")
        if self.is_published:
            self.validate_content()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Passage(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="passages", verbose_name="Тест")
    title = models.CharField(max_length=160, blank=True, verbose_name="Заголовок")
    text = models.TextField(max_length=12000, verbose_name="Текст для читання")

    class Meta:
        verbose_name = "Текст для читання"
        verbose_name_plural = "Тексти для читання"

    def __str__(self):
        return self.title or f"Текст №{self.pk}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Question(models.Model):
    class Kind(models.TextChoices):
        SINGLE = "single", "Одна правильна відповідь"
        MULTIPLE = "multiple", "Кілька правильних відповідей"
        NUMERIC = "numeric", "Числова відповідь"

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="questions", verbose_name="Тест")
    passage = models.ForeignKey(Passage, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions", verbose_name="Текст для читання")
    prompt = models.TextField(max_length=6000, blank=True, verbose_name="Запитання")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.SINGLE, verbose_name="Тип відповіді")
    topic = models.CharField(max_length=100, verbose_name="Тема")
    difficulty = models.CharField(max_length=10, choices=[("easy", "Базове"), ("medium", "Середнє"), ("hard", "Складне")], default="easy", verbose_name="Складність")
    explanation = models.TextField(max_length=6000, blank=True, verbose_name="Пояснення правильної відповіді")
    numeric_answer = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True, verbose_name="Правильне число")
    tolerance = models.DecimalField(max_digits=24, decimal_places=8, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))], verbose_name="Допустима абсолютна похибка")
    order = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)], verbose_name="Порядок")
    is_active = models.BooleanField(default=True, verbose_name="Включати до тесту")

    class Meta:
        verbose_name = "Запитання"
        verbose_name_plural = "Запитання"
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.assessment} · завдання {self.order}"

    def clean(self):
        super().clean()
        if self.passage_id:
            try:
                passage = self.passage
            except ObjectDoesNotExist:
                return  # Field validation reports an unknown passage ID.
            if passage.assessment_id != self.assessment_id:
                raise ValidationError("Текст для читання має належати цьому тесту.")

    def validate_content(self):
        self.full_clean()
        if not self.prompt.strip() or not self.explanation.strip():
            raise ValidationError(f"Завдання №{self.pk}: потрібні запитання й пояснення.")
        options = list(self.options.all())
        if self.kind == self.Kind.NUMERIC:
            if self.numeric_answer is None or options:
                raise ValidationError(f"Завдання №{self.pk}: задайте правильне число та приберіть варіанти відповіді.")
        else:
            correct = sum(option.is_correct for option in options)
            if not 2 <= len(options) <= 10 or any(not option.text.strip() for option in options):
                raise ValidationError(f"Завдання №{self.pk}: потрібно 2–10 заповнених варіантів.")
            if (self.kind == self.Kind.SINGLE and correct != 1) or (self.kind == self.Kind.MULTIPLE and correct < 2):
                raise ValidationError(f"Завдання №{self.pk}: перевірте кількість правильних відповідей.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Option(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options", verbose_name="Запитання")
    text = models.CharField(max_length=1000, verbose_name="Варіант відповіді")
    is_correct = models.BooleanField(default=False, verbose_name="Правильний")
    order = models.PositiveSmallIntegerField(default=1, verbose_name="Порядок")

    class Meta:
        verbose_name = "Варіант відповіді"
        verbose_name_plural = "Варіанти відповідей"
        ordering = ["order", "pk"]

    def __str__(self):
        return self.text

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ResultBand(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="result_bands", verbose_name="Тест")
    minimum_percent = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name="Відсоток від (включно)")
    title = models.CharField(max_length=160, verbose_name="Назва результату")
    feedback = models.TextField(max_length=4000, verbose_name="Рекомендації")
    level_hint = models.CharField(max_length=2, blank=True, choices=[(level, level) for level in ("A1", "A2", "B1", "B2")], verbose_name="Орієнтовний рівень", help_text="Показується тільки після методичної перевірки тесту.")

    class Meta:
        verbose_name = "Діапазон результату"
        verbose_name_plural = "Діапазони результатів"
        ordering = ["minimum_percent"]
        constraints = [models.UniqueConstraint(fields=["assessment", "minimum_percent"], name="unique_knowledge_band_threshold")]

    def __str__(self):
        return f"{self.assessment} · від {self.minimum_percent}%"

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class CourseRecommendation(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="recommendations", verbose_name="Тест")
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, verbose_name="Курс")
    minimum_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name="Відсоток від")
    maximum_percent = models.DecimalField(max_digits=5, decimal_places=2, default=100, validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name="Відсоток до (включно)")
    explanation = models.CharField(max_length=300, blank=True, verbose_name="Чому цей курс")
    order = models.PositiveSmallIntegerField(default=1, verbose_name="Порядок")

    class Meta:
        verbose_name = "Рекомендація курсу"
        verbose_name_plural = "Рекомендації курсів"
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.assessment} → {self.course}"

    def clean(self):
        super().clean()
        if self.minimum_percent > self.maximum_percent:
            raise ValidationError("Початок діапазону має бути не більшим за кінець.")
        if self.assessment_id and self.course_id:
            try:
                test, course = self.assessment, self.course
            except ObjectDoesNotExist:
                return  # full_clean reports the invalid foreign key itself.
            band = test.effective_band
            if course.subject != test.subject or (band and course.grade_band != band):
                raise ValidationError("Курс має відповідати предмету та класу / напряму тесту.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Attempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name="attempts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    owner_digest = models.CharField(max_length=64)
    snapshot = models.JSONField()
    answers = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["assessment", "completed_at"], name="knowledge_stats_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if previous.snapshot != self.snapshot or previous.assessment_id != self.assessment_id or previous.owner_digest != self.owner_digest or previous.user_id != self.user_id:
                raise ValidationError("Версію завдань і власника спроби змінювати не можна.")
            if previous.completed_at and any(getattr(previous, key) != getattr(self, key) for key in ("answers", "result", "percentage", "completed_at")):
                raise ValidationError("Завершений результат не можна переписувати.")
        super().save(*args, **kwargs)
