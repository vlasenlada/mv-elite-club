from django import forms
from scheduling.models import Lesson
from .models import Submission, Assignment



class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission

        fields = ["answer_text"]

        labels = {
            "answer_text": "Твоя відповідь"
        }

        widgets = {
            "answer_text": forms.Textarea(
                attrs={
                    "class": "homework-answer-input",
                    "placeholder": "Напиши свою відповідь тут...",
                    "rows": 8,
                }
            )
        }


class TeacherReviewForm(forms.Form):
    grade = forms.IntegerField(
        min_value=0,
        max_value=100,
        label="Оцінка"
    )

    feedback = forms.CharField(
        required=False,
        label="Коментар викладача",
        widget=forms.Textarea
    )


class TeacherAssignmentForm(forms.ModelForm):

    class Meta:
        model = Assignment

        fields = [
            "lesson",
            "title",
            "description",
            "due_at",
        ]

        widgets = {
            "title": forms.TextInput(
                attrs={
                    "placeholder": "Назва домашнього завдання"
                }
            ),

            "description": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Що потрібно виконати?"
                }
            ),

            "due_at": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local"
                },
                format="%Y-%m-%dT%H:%M"
            ),
        }

    
    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["lesson"].queryset = Lesson.objects.none()

        if teacher is not None:
            self.fields["lesson"].queryset = (
                Lesson.objects.filter(
                    teacher=teacher
                )
                .select_related(
                    "enrollment__student",
                    "enrollment__course"
                )
                .order_by("starts_at")
            )

        self.fields["lesson"].empty_label = "Оберіть заняття"

        self.fields["lesson"].label_from_instance = (
            self.get_lesson_label
        )

        self.fields["due_at"].input_formats = [
            "%Y-%m-%dT%H:%M"
        ]

    @staticmethod
    def get_lesson_label(lesson):
        from django.utils import timezone

        date = timezone.localtime(
            lesson.starts_at
        ).strftime("%d.%m.%Y, %H:%M")

        course = lesson.enrollment.course.title
        student = lesson.enrollment.student.username

        return f"{course} — {student} — {date}"
