
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from courses.models import Course, lesson_count_label


class Enrollment(models.Model):
    selected_package = models.ForeignKey("courses.CoursePackage", null=True, blank=True, on_delete=models.PROTECT,
        related_name="enrollments", verbose_name="Обраний пакет")
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
        if self.selected_package_id:
            package = self.selected_package
            if package.course_id != self.course_id or package.learning_format != self.requested_format:
                raise ValidationError("Пакет має відповідати курсу та обраному формату заявки.")
        if previous and previous.selected_package_id is not None and previous.selected_package_id != self.selected_package_id:
            raise ValidationError("Не змінюйте пакет існуючої заявки: збережіть погоджені умови та історію.")
        if (previous and (previous.student_id, previous.course_id) != (self.student_id, self.course_id)
                and (previous.selected_package_id or previous.lessons.exists() or previous.group_memberships.exists())):
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
        if self.selected_package_id:
            PackageAccount.objects.get_or_create(enrollment=self, defaults={
                "lesson_count": self.selected_package.lesson_count, "agreed_price": self.selected_package.price,
            })
        if previous and previous.status == self.Status.APPROVED and self.status != self.Status.APPROVED:
            for member in self.group_memberships.filter(is_active=True):
                member.is_active = False
                member.save(update_fields=["is_active"])

    def __str__(self):
        suffix = " · історична заявка" if not self.format_is_explicit else ""
        return f"{self.student.username} — {self.course.title} · {self.get_requested_format_display()}{suffix}"

    @property
    def current_lesson_price(self):
        return self.course.price_for_format(self.requested_format)


class PackageAccount(models.Model):
    class Status(models.TextChoices):
        UNCONFIRMED = "unconfirmed", "Очікує підтвердження"
        CONFIRMED = "confirmed", "Підтверджено адміністратором"
        CLOSED = "closed", "Закритий"
    enrollment = models.OneToOneField(Enrollment, on_delete=models.PROTECT, related_name="package_account", verbose_name="Заявка")
    lesson_count = models.PositiveSmallIntegerField(choices=[(n, str(n)) for n in (4, 8, 12)], verbose_name="Обсяг пакета")
    agreed_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Погоджена загальна ціна, грн")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNCONFIRMED, verbose_name="Статус обліку")

    class Meta:
        verbose_name = "Облік пакета"
        verbose_name_plural = "Облік пакетів"
        constraints = [models.CheckConstraint(condition=models.Q(lesson_count__in=[4, 8, 12]), name="account_size_4_8_12"),
            models.CheckConstraint(condition=models.Q(agreed_price__isnull=True) | models.Q(agreed_price__gte=0), name="nonnegative_agreed_package_price")]

    def __str__(self):
        return f"{self.enrollment} · {self.lesson_label}"

    @property
    def lesson_label(self):
        return lesson_count_label(self.lesson_count)

    @property
    def used_count(self):
        return self.usages.filter(lesson__status="completed").count()

    @property
    def remaining_count(self):
        return None if self.status == self.Status.UNCONFIRMED else max(0, self.lesson_count - self.used_count)

    @property
    def display_status(self):
        if self.status == self.Status.CONFIRMED and self.remaining_count == 0:
            return "Вичерпано"
        return self.get_status_display()

    def clean(self):
        super().clean()
        if not self.enrollment_id:
            return
        if not self.enrollment.selected_package_id:
            raise ValidationError("Для цієї заявки пакет не вибрано.")
        if self._state.adding and self.lesson_count != self.enrollment.selected_package.lesson_count:
            raise ValidationError("Обсяг обліку має відповідати вибраному пакету.")
        if self.status == self.Status.CONFIRMED and self.enrollment.status != "approved":
            raise ValidationError("Облік підтверджується лише для схваленої заявки. Це не підтвердження оплати.")
        if self.pk:
            old = PackageAccount.objects.get(pk=self.pk)
            if (old.enrollment_id, old.lesson_count) != (self.enrollment_id, self.lesson_count):
                raise ValidationError("Не змінюйте заявку або обсяг створеного обліку пакета.")
            if self.usages.exists() and self.status == self.Status.UNCONFIRMED:
                raise ValidationError("Пакет з історією використання не можна повернути до непідтвердженого стану.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk:
            PackageAccount.objects.select_for_update().get(pk=self.pk)
        self.full_clean()
        super().save(*args, **kwargs)


class PackageUsage(models.Model):
    account = models.ForeignKey(PackageAccount, on_delete=models.PROTECT, related_name="usages", verbose_name="Пакет учня")
    lesson = models.ForeignKey("scheduling.Lesson", on_delete=models.PROTECT, related_name="package_usages", verbose_name="Проведене заняття")
    note = models.CharField(max_length=300, verbose_name="Підстава підтвердження фактичного заняття")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Час підтвердження")

    class Meta:
        verbose_name = "Використане заняття пакета"
        verbose_name_plural = "Використані заняття пакетів"
        constraints = [models.UniqueConstraint(fields=["account", "lesson"], name="unique_account_lesson_usage")]

    def clean(self):
        super().clean()
        if not self.account_id or not self.lesson_id:
            return
        account, lesson = self.account, self.lesson
        enrollment = account.enrollment
        if account.status != "confirmed" or enrollment.status != "approved":
            raise ValidationError("Потрібні підтверджений облік пакета та активна заявка. Автоматичної оплати немає.")
        if lesson.status != "completed" or lesson.ends_at > timezone.now():
            raise ValidationError("Підтверджуйте лише фактично проведене заняття після його завершення.")
        if lesson.course.pk != enrollment.course_id or not lesson.has_student(enrollment.student):
            raise ValidationError("Це заняття не належить учню та курсу пакета.")
        if bool(lesson.group_id) != (enrollment.requested_format == "group"):
            raise ValidationError("Формат заняття не відповідає пакету.")
        if lesson.group_id and not lesson.attendances.filter(membership__enrollment=enrollment, status="present").exists():
            raise ValidationError("Для групи підтвердіть присутність. Пропуски й запізнення поки не списуються.")
        if self._state.adding and account.used_count >= account.lesson_count:
            raise ValidationError("У пакеті немає невикористаних занять.")
        if self.pk:
            old = PackageUsage.objects.get(pk=self.pk)
            if (old.account_id, old.lesson_id) != (self.account_id, self.lesson_id):
                raise ValidationError("Не переприв'язуйте історію використання пакета.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.account = PackageAccount.objects.select_for_update().get(pk=self.account_id)
        self.full_clean()
        super().save(*args, **kwargs)


# Registered with the existing app, separate from regular Enrollment records.
from .trial_models import TrialGroupAdmission, TrialRequest  # noqa: E402, F401
