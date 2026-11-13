
from django import forms

from enrollments.models import Enrollment
from .models import Lesson


class TeacherLessonForm(forms.ModelForm):

    class Meta:
        model = Lesson

        fields = [
            "enrollment",
            "starts_at",
            "ends_at",
            "meeting_url",
        ]

        labels = {
            "enrollment": "Учень та курс",
            "starts_at": "Початок заняття",
            "ends_at": "Завершення заняття",
            "meeting_url": "Посилання на урок",
        }

        widgets = {
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M"
            ),
            "meeting_url": forms.URLInput(
                attrs={
                    "placeholder": "https://meet.google.com/..."
                }
            ),
        }

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)

        
        # Учні, яких адміністрація призначила викладачу
        enrollments = Enrollment.objects.none()

        if teacher is not None:
            # ModelForm validates the model before the view calls save().
            self.instance.teacher = teacher
            enrollments = Enrollment.objects.filter(
                teacher=teacher,
                status=Enrollment.Status.APPROVED
            )

        self.fields["enrollment"].queryset = (
            enrollments.select_related("student", "course")
        )


        self.fields["enrollment"].empty_label = (
            "Оберіть учня та курс"
        )

        self.fields["enrollment"].label_from_instance = (
            lambda enrollment: (
                f"{enrollment.student.username} — "
                f"{enrollment.course.title}"
            )
        )

        for field_name in ("starts_at", "ends_at"):
            self.fields[field_name].input_formats = [
                "%Y-%m-%dT%H:%M"
            ]

        self.fields["meeting_url"].required = False

    def clean(self):
        cleaned_data = super().clean()

        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")

        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error(
                "ends_at",
                "Завершення має бути пізніше за початок."
            )

        return cleaned_data


class TeacherMeetingLinkForm(forms.ModelForm):

    class Meta:
        model = Lesson

        fields = ["meeting_url"]

        labels = {
            "meeting_url": "Посилання на Google Meet / Zoom"
        }

        widgets = {
            "meeting_url": forms.URLInput(
                attrs={
                    "placeholder": "https://meet.google.com/...",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["meeting_url"].required = False

    def clean_meeting_url(self):
        url = self.cleaned_data.get("meeting_url", "")

        if url and not url.startswith("https://"):
            raise forms.ValidationError(
                "Посилання має починатися з https://"
            )

        return url



class TeacherLessonEditForm(forms.ModelForm):

    class Meta:
        model = Lesson

        fields = [
            "starts_at",
            "ends_at",
            "meeting_url",
        ]

        labels = {
            "starts_at": "Початок заняття",
            "ends_at": "Завершення заняття",
            "meeting_url": "Google Meet / Zoom",
        }

        widgets = {
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M"
            ),

            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M"
            ),

            "meeting_url": forms.URLInput(
                attrs={
                    "placeholder": "https://meet.google.com/..."
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field_name in ("starts_at", "ends_at"):
            self.fields[field_name].input_formats = [
                "%Y-%m-%dT%H:%M"
            ]

        self.fields["meeting_url"].required = False

    def clean(self):
        cleaned_data = super().clean()

        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")

        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error(
                "ends_at",
                "Час завершення має бути пізніше за початок."
            )

        return cleaned_data

    def clean_meeting_url(self):
        url = self.cleaned_data.get("meeting_url", "")

        if url and not url.startswith("https://"):
            raise forms.ValidationError(
                "Посилання має починатися з https://"
            )

        return url
