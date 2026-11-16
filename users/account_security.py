import hashlib
import secrets
import unicodedata
from datetime import timedelta

from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.db import models, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import AccountRequestThrottle, EmailConfirmation, User


def normalize_email(email):
    return unicodedata.normalize("NFKC", email.strip()).casefold()


def email_owners(email):
    normalized = normalize_email(email)
    ids = [pk for pk, address in User.objects.exclude(email="").values_list("pk", "email")
           if normalize_email(address) == normalized]
    return User.objects.filter(pk__in=ids)


def verified_account(email):
    email = normalize_email(email)
    # Even inactive or unverified duplicates make ownership ambiguous.
    matches = list(email_owners(email)[:2])
    if len(matches) != 1:
        return None
    user = matches[0]
    if user.is_active and user.has_usable_password() and user.verified_email == email:
        return user
    return None


class VerifiedResetTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        return super()._make_hash_value(user, timestamp) + (user.verified_email or "")


reset_tokens = VerifiedResetTokenGenerator()


class VerifiedPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].label = "Електронна пошта"

    def get_users(self, email):
        user = verified_account(email)
        return [user] if user else []


def allow_request(purpose, identity, *, limit, seconds):
    now = timezone.now()
    bucket = int(now.timestamp()) // seconds
    key = salted_hmac("account-throttle", f"{purpose}:{identity}:{bucket}").hexdigest()
    row, _ = AccountRequestThrottle.objects.get_or_create(key=key,
        defaults={"expires_at": now + timedelta(seconds=seconds)})
    return AccountRequestThrottle.objects.filter(pk=row.pk, count__lt=limit).update(count=models.F("count") + 1) == 1


def allow_reset(request, email):
    ip_allowed = allow_request("reset-ip", request.META.get("REMOTE_ADDR", ""), limit=5, seconds=900)
    if not ip_allowed:
        return False
    return allow_request("reset-email", normalize_email(email), limit=3, seconds=3600)


def request_email_confirmation(user, email, request):
    email = normalize_email(email)
    if not allow_request("confirm-user", str(user.pk), limit=5, seconds=3600):
        return False
    if email_owners(email).exclude(pk=user.pk).exists():
        return False
    token = secrets.token_urlsafe(32)
    EmailConfirmation.objects.update_or_create(user=user, defaults={
        "email": email, "original_email": user.email,
        "password_digest": hashlib.sha256(user.password.encode()).hexdigest(),
        "token_digest": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": timezone.now() + timedelta(hours=24),
    })
    url = request.build_absolute_uri(reverse("confirm_email", args=[token]))
    body = render_to_string("users/email_confirmation_email.txt", {"confirmation_url": url})
    # Delivery is deliberately not claimed if the configured backend fails.
    try:
        send_mail("Підтвердження пошти — M&V Elite Club", body, None, [email])
    except Exception:
        return False
    return True


@transaction.atomic
def confirm_email(user, token):
    user = User.objects.select_for_update().get(pk=user.pk)
    confirmation = EmailConfirmation.objects.select_for_update().filter(user=user).first()
    if not confirmation or confirmation.expires_at <= timezone.now():
        return False
    if not secrets.compare_digest(confirmation.token_digest, hashlib.sha256(token.encode()).hexdigest()):
        return False
    if confirmation.original_email != user.email or confirmation.password_digest != hashlib.sha256(user.password.encode()).hexdigest():
        return False
    if email_owners(confirmation.email).exclude(pk=user.pk).exists():
        return False
    user.email = user.verified_email = confirmation.email
    user.save(update_fields=["email", "verified_email"])
    confirmation.delete()  # Disposable token only, never educational history.
    return True
