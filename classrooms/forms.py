from django import forms

from scheduling.models import Lesson

from .models import Attendance, StudyGroup


class GroupLessonForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = ["group", "starts_at", "ends_at", "meeting_url"]
        widgets = {
            name: forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
            for name in ("starts_at", "ends_at")
        }

    def __init__(self, *args, teacher, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.teacher = teacher
        self.fields["group"].required = True
        self.fields["group"].queryset = StudyGroup.objects.filter(teacher=teacher, is_active=True)
        for name in ("starts_at", "ends_at"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M"]


class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ["status", "note"]
