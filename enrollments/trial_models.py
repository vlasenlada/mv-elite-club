"""Trial enquiries are separate from account-bound, regular enrollments."""
import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from courses.models import Course
from users.account_security import normalize_email
from users.teacher_assignment import get_assignment_teacher


def normalize_phone(value):
    value = value.strip()
    if not re.fullmatch(r"\+?[0-9\s().-]+", value):
        raise ValidationError("Вкажіть номер із 7–15 цифр; можна використати + на початку.")
    digits = re.sub(r"[^0-9]", "", value)
    if not 7 <= len(digits) <= 15:
        raise ValidationError("Вкажіть номер із 7–15 цифр.")
    return ("+" if value.startswith("+") else "") + digits


def identity_key(kind, value):
    return salted_hmac("trial-enquiry-" + kind, value).hexdigest()


class TrialGroupAdmission(models.Model):
    class Stage(models.TextChoices):
        CLOSED = "closed", "Пробний набір закрито"
        FORMING = "forming", "Група формується"
        STARTING = "starting", "Початок навчання"

    group = models.OneToOneField("classrooms.StudyGroup", on_delete=models.CASCADE, related_name="trial_admission", verbose_name="Група")
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.CLOSED, verbose_name="Етап пробного набору")
    accepting_until = models.DateField(null=True, blank=True, verbose_name="Приймати пробні заявки до (включно)")

    class Meta:
        verbose_name = "Умови пробного набору групи"
        verbose_name_plural = "Умови пробного набору груп"

    def clean(self):
        super().clean()
        if self.stage != self.Stage.CLOSED and (not self.accepting_until or self.accepting_until < timezone.localdate()):
            raise ValidationError({"accepting_until": "Для відкритого набору вкажіть чинну кінцеву дату. Відкривайте набір лише для нової групи або початку програми."})

    def __str__(self):
        return f"Пробний набір групи №{self.group_id}"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class TrialRequest(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Нова"
        CONTACTED = "contacted", "Узгоджуємо умови"
        APPROVED = "approved", "Участь підтверджено"
        COMPLETED = "completed", "Пробне проведено"
        REJECTED = "rejected", "Відхилено"
        CANCELLED = "cancelled", "Скасовано"

    ACTIVE = ("new", "contacted", "approved")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="trial_requests", verbose_name="Користувач, який подав заявку")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="trial_requests", verbose_name="Курс")
    requested_format = models.CharField(max_length=20, choices=Course.LessonFormat.choices, verbose_name="Бажаний формат")
    student_name = models.CharField(max_length=120, verbose_name="Ім’я учня")
    level = models.CharField(max_length=100, verbose_name="Клас або рівень підготовки")
    is_minor = models.BooleanField(verbose_name="Учень неповнолітній")
    guardian_name = models.CharField(max_length=120, blank=True, verbose_name="Ім’я одного з батьків / законного представника")
    email = models.EmailField(verbose_name="Контактна електронна пошта")
    phone = models.CharField(max_length=24, verbose_name="Телефон", validators=[RegexValidator(r"^\+?[0-9]{7,15}$", "Вкажіть коректний номер телефону.")])
    comment = models.TextField(max_length=1500, blank=True, verbose_name="Коментар заявника")
    consent_at = models.DateTimeField(editable=False, verbose_name="Згода на обробку контактних даних")
    guardian_consent_at = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Підтвердження законного представництва")
    consent_version = models.CharField(max_length=32, default="trial-2026-11-v1", editable=False)
    student_key = models.CharField(max_length=40, editable=False)
    email_key = models.CharField(max_length=40, editable=False)
    phone_key = models.CharField(max_length=40, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, verbose_name="Статус")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_trial_requests", verbose_name="Викладач")
    group = models.ForeignKey("classrooms.StudyGroup", null=True, blank=True, on_delete=models.PROTECT, related_name="trial_requests", verbose_name="Група для погодження")
    group_level_confirmed = models.BooleanField(default=False, verbose_name="Адміністрація перевірила рівень учня та початковий етап програми")
    staff_note = models.TextField(blank=True, max_length=2000, verbose_name="Внутрішня примітка адміністрації", help_text="Узгодьте дату й час із заявником та викладачем. Ця заявка не створює заняття в календарі.")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Подано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Оновлено")

    class Meta:
        verbose_name = "Заявка на безкоштовне пробне"
        verbose_name_plural = "Заявки на безкоштовні пробні"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["course", "student_key", "email_key"], condition=models.Q(status__in=["new", "contacted", "approved"]), name="unique_active_trial_email"),
            models.UniqueConstraint(fields=["course", "student_key", "phone_key"], condition=models.Q(status__in=["new", "contacted", "approved"]), name="unique_active_trial_phone"),
        ]

    def __str__(self):
        # Django Admin audit logs must not contain contact details or names.
        return f"Пробна заявка №{self.pk or 'нова'} · курс №{self.course_id}"

    def update_identity(self):
        self.email = normalize_email(self.email)
        self.phone = normalize_phone(self.phone)
        name = " ".join(unicodedata.normalize("NFKC", self.student_name).casefold().split())
        self.student_key = identity_key("student", name)
        self.email_key = identity_key("email", self.email)
        self.phone_key = identity_key("phone", re.sub(r"\D", "", self.phone))

    def clean(self):
        super().clean()
        self.update_identity()
        if self.is_minor and (not self.guardian_name.strip() or not self.guardian_consent_at):
            raise ValidationError("Для неповнолітнього потрібні ім’я та згода законного представника.")
        if not self.consent_at:
            raise ValidationError("Потрібна згода на обробку контактних даних.")
        if self.course_id and not {"individual": self.course.supports_individual, "group": self.course.supports_group}.get(self.requested_format):
            raise ValidationError("Обраний формат недоступний для цього курсу.")
        if self.group_id and (self.requested_format != "group" or self.group.course_id != self.course_id):
            raise ValidationError("Група має відповідати курсу та груповому формату.")
        if self.status in self.ACTIVE:
            from .trial_services import duplicate_trial
            if duplicate_trial(self):
                raise ValidationError("Активну пробну заявку з такими даними вже збережено.")
        if self.teacher_id:
            get_assignment_teacher(self.teacher_id)
        if self.status == self.Status.APPROVED:
            if self.requested_format == "group":
                group = self.group if self.group_id else None
                policy = TrialGroupAdmission.objects.filter(group=group).first() if group else None
                if not group or not group.is_active or not policy or policy.stage not in {"forming", "starting"} or not policy.accepting_until or policy.accepting_until < timezone.localdate():
                    raise ValidationError("Пробне доступне лише для активної групи з відкритим набором на етапі формування або початку навчання.")
                if not self.group_level_confirmed:
                    raise ValidationError("Підтвердьте відповідність рівня учня та початкового етапу програми.")
                pending = type(self).objects.filter(group=group, status="approved").exclude(pk=self.pk).count()
                if group.capacity is None or group.free_places <= pending:
                    raise ValidationError("Для пробного немає вільних місць. Перевірте місткість, учасників та інші підтверджені пробні заявки.")
                if self.teacher_id != group.teacher_id:
                    raise ValidationError("Для групового пробного оберіть викладача цієї групи.")
            if not self.teacher_id:
                raise ValidationError("Для підтвердження участі призначте викладача й узгодьте дату з заявником.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        # Lock the existing group when confirming a trial; never add a membership.
        if self.group_id:
            from classrooms.models import StudyGroup
            self.group = StudyGroup.objects.select_for_update().get(pk=self.group_id)
        self.update_identity()
        self.full_clean()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"student_key", "email_key", "phone_key", "email", "phone", "updated_at"}
        super().save(*args, **kwargs)
