
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    verified_email = models.EmailField(null=True, blank=True, editable=False, verbose_name="Підтверджена електронна пошта")

    class Meta(AbstractUser.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(Lower("verified_email"), name="unique_verified_email_ci")]

    def save(self, *args, **kwargs):
        if self.verified_email and self.verified_email.casefold() != self.email.strip().casefold():
            self.verified_email = None
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"verified_email"}
        super().save(*args, **kwargs)
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


class EmailConfirmation(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="email_confirmation")
    email = models.EmailField()
    original_email = models.EmailField(blank=True)
    password_digest = models.CharField(max_length=64)
    token_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()


class AccountRequestThrottle(models.Model):
    key = models.CharField(max_length=128, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
