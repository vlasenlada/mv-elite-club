from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from users.teacher_assignment import get_assignment_teacher


class StudyGroup(models.Model):
    capacity = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)], verbose_name="Максимальна кількість учнів",
        help_text="Будь-яке додатне ціле число. Для історичної групи без ліміту вкажіть місткість вручну.",
    )
    name = models.CharField(max_length=160, verbose_name="Назва групи")
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="study_groups", verbose_name="Курс")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="study_groups", verbose_name="Викладач")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Навчальна група"
        verbose_name_plural = "Навчальні групи"
        ordering = ["name", "pk"]
        constraints = [models.CheckConstraint(condition=Q(capacity__isnull=True) | Q(capacity__gt=0), name="positive_group_capacity")]

    def __str__(self):
        return f"{self.name} · {self.course.title}"

    def clean(self):
        super().clean()
        self._transfer_lessons = []
        if isinstance(self.capacity, int) and self.capacity < self.member_count:
            raise ValidationError({"capacity": "Місткість не може бути меншою за кількість активних учасників. Спочатку завершіть їхню участь."})
        previous = StudyGroup.objects.filter(pk=self.pk).first() if self.pk else None
        if previous and previous.capacity is not None and self.capacity is None:
            raise ValidationError({"capacity": "Вкажіть додатну місткість. Уже встановлений ліміт не можна прибрати."})
        if self.teacher_id and (not previous or previous.teacher_id != self.teacher_id):
            get_assignment_teacher(self.teacher_id)
        if self.course_id and not self.course.supports_group:
            raise ValidationError("Цей курс не підтримує групове навчання.")
        if previous and previous.course_id != self.course_id:
            raise ValidationError("Не змінюйте курс існуючої групи. Створіть іншу групу, зберігши історію.")
        if previous and previous.teacher_id != self.teacher_id:
            from .services import plan_group_transfer
            self._transfer_lessons = plan_group_transfer(self)

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk:
            StudyGroup.objects.select_for_update().get(pk=self.pk)
        self.full_clean()
        super().save(*args, **kwargs)
        for lesson in self._transfer_lessons:
            lesson.save(update_fields=["teacher"])

    @property
    def member_count(self):
        return self.memberships.filter(is_active=True).count() if self.pk else 0

    @property
    def free_places(self):
        return None if self.capacity is None else max(self.capacity - self.member_count, 0)


class GroupMembership(models.Model):
    group = models.ForeignKey(StudyGroup, on_delete=models.PROTECT, related_name="memberships", verbose_name="Група")
    enrollment = models.ForeignKey("enrollments.Enrollment", on_delete=models.PROTECT, related_name="group_memberships", verbose_name="Заявка учня")
    is_active = models.BooleanField(default=True, verbose_name="Активний учасник")
    joined_at = models.DateTimeField(default=timezone.now, editable=False)
    left_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Учасник групи"
        verbose_name_plural = "Учасники груп"
        constraints = [models.UniqueConstraint(fields=["enrollment"], condition=Q(is_active=True), name="one_active_group_per_enrollment")]

    def __str__(self):
        return f"{self.enrollment.student.username} · {self.group.name}"

    def clean(self):
        super().clean()
        if not self.enrollment_id:
            return
        try:
            group = self.group
        except StudyGroup.DoesNotExist:
            return
        if not group.course_id:
            return
        previous = GroupMembership.objects.filter(pk=self.pk).first() if self.pk else None
        if previous and (previous.group_id, previous.enrollment_id) != (self.group_id, self.enrollment_id):
            raise ValidationError("Не переприв'язуйте історію учасника. Завершіть участь і створіть новий запис.")
        if previous and not previous.is_active and self.is_active:
            raise ValidationError("Для повторного вступу створіть новий запис участі — історія попереднього періоду збережеться.")
        if self.enrollment.student.role != "student" or self.enrollment.student.is_staff:
            raise ValidationError("Учасником групи може бути лише учень.")
        if self.enrollment.course_id != group.course_id:
            raise ValidationError("Група та заявка мають належати одному курсу.")
        if self.is_active and (not previous or not previous.is_active):
            if self.enrollment.format_is_explicit and self.enrollment.requested_format != "group":
                raise ValidationError("Ця заявка обрана для індивідуального навчання, а не для групи.")
            if self.enrollment.status != "approved" or not group.is_active:
                raise ValidationError("Додати учня можна лише за підтвердженою заявкою до активної групи.")
            if group.capacity is not None and group.member_count >= group.capacity:
                raise ValidationError("У групі немає вільних місць. Оберіть іншу групу або збільште місткість.")
            if group.pk:
                for lesson in group.lessons.filter(status="scheduled", starts_at__gte=timezone.now()):
                    lesson.validate_schedule(student_ids=[self.enrollment.student_id])

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.group_id:
            self.group = StudyGroup.objects.select_for_update().get(pk=self.group_id)
        previous = GroupMembership.objects.filter(pk=self.pk).first() if self.pk else None
        if previous is None and not self.is_active:
            self.left_at = self.joined_at
        if previous and previous.is_active and not self.is_active:
            self.left_at = timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"left_at"}
        self.full_clean()
        super().save(*args, **kwargs)
        if self.is_active and self.enrollment.status == "approved":
            from .services import sync_attendance
            for lesson in self.group.lessons.filter(status="scheduled", starts_at__gte=self.joined_at):
                sync_attendance(lesson)


class Attendance(models.Model):
    class Status(models.TextChoices):
        UNMARKED = "unmarked", "Не відмічено"
        PRESENT = "present", "Присутній / присутня"
        ABSENT = "absent", "Відсутній / відсутня"
        LATE = "late", "Запізнення"
        EXCUSED = "excused", "Поважна причина"

    lesson = models.ForeignKey("scheduling.Lesson", on_delete=models.PROTECT, related_name="attendances", verbose_name="Заняття")
    membership = models.ForeignKey(GroupMembership, on_delete=models.PROTECT, related_name="attendances", verbose_name="Учасник")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNMARKED, verbose_name="Відвідування")
    note = models.CharField(max_length=300, blank=True, verbose_name="Примітка")

    class Meta:
        verbose_name = "Відвідування"
        verbose_name_plural = "Відвідування"
        constraints = [models.UniqueConstraint(fields=["lesson", "membership"], name="unique_lesson_member_attendance")]

    def __str__(self):
        return f"{self.membership} · {self.get_status_display()}"

    def clean(self):
        super().clean()
        if self.lesson_id and self.membership_id:
            member = self.membership
            if (self.lesson.group_id != member.group_id or member.joined_at > self.lesson.starts_at
                    or (member.left_at is not None and member.left_at <= self.lesson.starts_at)):
                raise ValidationError("Учасник не належить до складу групи на дату цього заняття.")
