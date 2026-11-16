
from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User
from .teacher_assignment import TEACHER_ERROR, eligible_teachers


class StudentRegistrationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        label="Електронна пошта"
    )

    def clean_email(self):
        from .account_security import email_owners, normalize_email
        email = normalize_email(self.cleaned_data["email"])
        if email_owners(email).exists():
            raise forms.ValidationError("Цю адресу неможливо використати. Увійдіть або зверніться до адміністрації.")
        return email

    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "password1",
            "password2",
        )
        labels = {
            "username": "Логін",
            "first_name": "Ім'я",
            "last_name": "Прізвище",
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.STUDENT
        user.is_staff = False
        user.is_superuser = False

        if commit:
            user.save()

        return user


class TeacherChoiceForm(forms.Form):
    teacher_id = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Викладач",
        error_messages={
            "required": "Оберіть викладача.",
            "invalid_choice": TEACHER_ERROR,
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teacher_id"].queryset = eligible_teachers()
