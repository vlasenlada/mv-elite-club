
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", "Учень"
        TEACHER = "teacher", "Викладач"
        ADMIN = "admin", "Адміністратор"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STUDENT,
        verbose_name="Роль користувача",
    )

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
