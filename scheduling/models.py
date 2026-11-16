
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from enrollments.models import Enrollment
from django.conf import settings

class LessonQuerySet(models.QuerySet):
    def active_learning(self):
        return self.filter(Q(enrollment__status="approved") | Q(group__is_active=True))

    def for_student(self, student, *, current=False):
        from classrooms.models import GroupMembership
        student_id = getattr(student, "pk", student)
        members = GroupMembership.objects.filter(
            group_id=OuterRef("group_id"), enrollment__student_id=student_id,
            joined_at__lte=OuterRef("starts_at"),
        ).filter(Q(left_at__isnull=True) | Q(left_at__gt=OuterRef("starts_at")))
        individual = Q(enrollment__student_id=student_id)
        if current:
            members = members.filter(is_active=True, enrollment__status="approved", group__is_active=True)
            individual &= Q(enrollment__status="approved")
        return self.annotate(_student_group_access=Exists(members)).filter(individual | Q(_student_group_access=True))


class Lesson(models.Model):
    objects = LessonQuerySet.as_manager()
    group = models.ForeignKey(
        "classrooms.StudyGroup", on_delete=models.PROTECT, null=True, blank=True,
        related_name="lessons", verbose_name="Навчальна група",
    )

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Заплановане"
        COMPLETED = "completed", "Проведене"
        CANCELLED = "cancelled", "Скасоване"

    enrollment = models.ForeignKey(
        Enrollment,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="lessons",
        verbose_name="Запис учня на курс"
    )
    
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teaching_lessons",
        verbose_name="Викладач"
    )


    starts_at = models.DateTimeField(
        verbose_name="Дата та час початку"
    )

    ends_at = models.DateTimeField(
        verbose_name="Дата та час завершення"
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        verbose_name="Статус заняття"
    )

    meeting_url = models.URLField(
        blank=True,
        verbose_name="Посилання на онлайн-заняття"
    )

    class Meta:
        verbose_name = "Заняття"
        verbose_name_plural = "Заняття"
        ordering = ["starts_at"]
        constraints = [models.CheckConstraint(
            condition=(Q(enrollment__isnull=False, group__isnull=True) | Q(enrollment__isnull=True, group__isnull=False)),
            name="lesson_individual_or_group",
        )]

    def clean(self):
        super().clean()

        if bool(self.enrollment_id) == bool(self.group_id):
            raise ValidationError("Оберіть або індивідуальну заявку, або навчальну групу.")
        if self.group_id:
            if not self.group.is_active:
                raise ValidationError("Створення та редагування занять архівної групи недоступні.")
            if self._state.adding and not self.teacher_id:
                self.teacher = self.group.teacher
        if self._state.adding and self.enrollment_id and not self.enrollment.course.supports_individual:
            raise ValidationError("Цей курс не підтримує індивідуальні заняття.")
        if (self._state.adding and self.enrollment_id and self.enrollment.format_is_explicit
                and self.enrollment.requested_format == "group"):
            raise ValidationError("Ця заявка обрана для групового навчання. Створіть спільне заняття групи.")
        if self.pk:
            previous = Lesson.objects.filter(pk=self.pk).first()
            if previous and (previous.enrollment_id, previous.group_id) != (self.enrollment_id, self.group_id):
                raise ValidationError("Не змінюйте учня, групу або тип існуючого заняття — його навчальна історія має зберігатися.")

        if (
            self.starts_at
            and self.ends_at
            and self.ends_at <= self.starts_at
        ):
            raise ValidationError({
                "ends_at": "Час завершення має бути пізніше початку."
            })

        if (
            self.enrollment_id
            and self.enrollment.status != Enrollment.Status.APPROVED
        ):
            # This rule also applies to forms that do not expose enrollment.
            raise ValidationError(
                "Заявка учня не підтверджена або скасована. "
                "Створення та редагування заняття недоступні.",
                code="enrollment_not_approved",
            )

        self.validate_schedule()
        self.validate_teacher_change()

    def validate_teacher_change(self):
        previous = None
        if self.pk:
            previous = Lesson.objects.filter(pk=self.pk).first()
        if previous and previous.teacher_id == self.teacher_id:
            return
        if not self.teacher_id and previous is None:
            return

        from users.teacher_assignment import get_assignment_teacher
        get_assignment_teacher(self.teacher_id)
        if previous:
            now = timezone.now()
            if (
                previous.status != self.Status.SCHEDULED
                or self.status != self.Status.SCHEDULED
                or previous.starts_at < now
                or self.starts_at < now
            ):
                raise ValidationError(
                    "Змінити викладача можна лише для майбутнього запланованого заняття. "
                    "Історію занять змінювати заборонено."
                )
            if self.assignments.exists():
                raise ValidationError(
                    "Неможливо змінити викладача: для цього заняття вже є домашні завдання."
                )

    def validate_schedule(self, *, student_ids=None):
        if self.pk and self.status == self.Status.COMPLETED:
            for usage in self.package_usages.select_related("account"):
                if usage.account.usages.filter(lesson__status="completed").exclude(lesson_id=self.pk).count() >= usage.account.lesson_count:
                    raise ValidationError("Повернення цього заняття перевищить обсяг пакета. Узгодьте облік з адміністрацією.")
        if (
            self.status == self.Status.CANCELLED
            or not self.starts_at
            or not self.ends_at
            or self.ends_at <= self.starts_at
        ):
            return

        # Adjacent lessons are allowed: intervals are [starts_at, ends_at).
        overlapping = Lesson.objects.filter(
            starts_at__lt=self.ends_at,
            ends_at__gt=self.starts_at,
        ).exclude(status=self.Status.CANCELLED)
        if self.pk is not None:
            overlapping = overlapping.exclude(pk=self.pk)

        errors = []
        if self.teacher_id and overlapping.filter(teacher_id=self.teacher_id).exists():
            errors.append(ValidationError(
                "Викладач уже має інше заняття в цей час. Оберіть інший час або викладача.",
                code="teacher_schedule_conflict",
            ))
        if student_ids is None:
            student_ids = ([self.enrollment.student_id] if self.enrollment_id else
                           list(self.memberships_at_start().filter(enrollment__status="approved").values_list("enrollment__student_id", flat=True)))
        if any(overlapping.for_student(student_id).exists() for student_id in student_ids):
            errors.append(ValidationError(
                "Учень уже має інше заняття в цей час. Оберіть інший час.",
                code="student_schedule_conflict",
            ))
        if self.group_id and overlapping.filter(group_id=self.group_id).exists():
            errors.append(ValidationError("Група вже має заняття в цей час.", code="group_schedule_conflict"))
        if errors:
            # Non-field errors work in both partial forms and Django Admin.
            raise ValidationError(errors)

    def __str__(self):
        if self.group_id:
            return f"{self.group.name} — {self.group.course.title}"
        return (
            f"{self.enrollment.student.username} — "
            f"{self.enrollment.course.title}"
        )

    @property
    def course(self):
        return self.enrollment.course if self.enrollment_id else self.group.course

    @property
    def audience_label(self):
        return self.enrollment.student.username if self.enrollment_id else self.group.name

    @property
    def is_active_for_learning(self):
        return self.enrollment.status == "approved" if self.enrollment_id else self.group.is_active

    def memberships_at_start(self):
        from classrooms.models import GroupMembership
        return GroupMembership.objects.filter(
            group_id=self.group_id, joined_at__lte=self.starts_at,
        ).filter(Q(left_at__isnull=True) | Q(left_at__gt=self.starts_at))

    def has_student(self, student, *, current=False):
        if self.enrollment_id:
            return self.enrollment.student_id == student.pk and (not current or self.enrollment.status == "approved")
        if not self.group_id or (current and not self.group.is_active):
            return False
        members = self.memberships_at_start().filter(enrollment__student=student)
        if current:
            members = members.filter(is_active=True, enrollment__status="approved")
        return members.exists()

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.group_id:
            from classrooms.services import sync_attendance
            sync_attendance(self)
