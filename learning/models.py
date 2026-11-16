
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Exists, F, OuterRef, Q



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

    def clean(self):
        super().clean()
        if (
            self._state.adding
            and self.lesson_id
            and not self.lesson.is_active_for_learning
        ):
            raise ValidationError(
                "Нове домашнє завдання можна створити лише для підтвердженої заявки.",
                code="enrollment_not_approved",
            )

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Домашнє завдання"
        verbose_name_plural = "Домашні завдання"


class SubmissionQuerySet(models.QuerySet):
    def with_participant_history(self):
        from classrooms.models import GroupMembership
        members = GroupMembership.objects.filter(
            group_id=OuterRef("assignment__lesson__group_id"),
            enrollment__student_id=OuterRef("student_id"),
        ).filter(
            (Q(joined_at__lte=OuterRef("assignment__lesson__starts_at"))
             & (Q(left_at__isnull=True) | Q(left_at__gt=OuterRef("assignment__lesson__starts_at"))))
            | (Q(joined_at__lte=OuterRef("submitted_at"))
               & (Q(left_at__isnull=True) | Q(left_at__gt=OuterRef("submitted_at"))))
        )
        return self.annotate(_participant_history=Exists(members)).filter(
            Q(assignment__lesson__enrollment__student_id=F("student_id")) | Q(_participant_history=True)
        )


class Submission(models.Model):
    objects = SubmissionQuerySet.as_manager()
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

    def has_participant_history(self):
        """Keep received work reviewable after a member leaves a future lesson."""
        lesson = self.assignment.lesson
        if lesson.has_student(self.student):
            return True
        if not lesson.group_id or not self.submitted_at:
            return False
        return lesson.group.memberships.filter(
            enrollment__student_id=self.student_id,
            joined_at__lte=self.submitted_at,
        ).filter(Q(left_at__isnull=True) | Q(left_at__gt=self.submitted_at)).exists()

    def clean(self):
        super().clean()
        if self.assignment_id and self.student_id:
            valid_participant = (self.assignment.lesson.has_student(self.student, current=True)
                                 if self._state.adding else self.has_participant_history())
            if not valid_participant:
                raise ValidationError("Відповідь можна зберегти лише для учня цього заняття.")

    class Meta:
        verbose_name = "Виконана робота"
        verbose_name_plural = "Виконані роботи"
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "student"],
                name="unique_student_assignment"
            )
        ]
