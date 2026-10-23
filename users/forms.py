
from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class StudentRegistrationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        label="Електронна пошта"
    )

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
