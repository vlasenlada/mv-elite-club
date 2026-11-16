from datetime import datetime, timedelta
import re
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .account_security import request_email_confirmation, reset_tokens, verified_account
from .models import EmailConfirmation, User


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AccountSecurityTests(TestCase):
    password = "Account-TestOnly-K8!x45"
    new_password = "Changed-TestOnly-M3!u87"

    def user(self, username="secure_student", role="student", verified=True, email=None):
        user = User.objects.create_user(username=username, password=self.password,
            role=role, email=email or username + "@example.invalid", is_staff=role == "admin")
        if verified:
            user.verified_email = user.email.strip().casefold()
            user.save(update_fields=["verified_email"])
        return user

    def request_reset(self, email, client=None):
        return (client or self.client).post(reverse("password_reset"), {"email": email})

    def reset_url(self, user):
        return reverse("password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(user.pk)), reset_tokens.make_token(user)])

    def email_token(self):
        return re.search(r'/profile/email/([^/\s]+)/', mail.outbox[-1].body).group(1)

    def profile_request(self, user, email=None, password=None):
        self.client.force_login(user)
        return self.client.post(reverse("profile"), {"email": email or user.email,
            "current_password": password if password is not None else self.password})

    def test_password_reset_end_to_end_for_all_roles_and_token_cannot_be_reused(self):
        for role in ("student", "teacher", "admin"):
            with self.subTest(role=role):
                user = self.user(username="reset_" + role, role=role)
                self.assertRedirects(self.request_reset(user.email), reverse("password_reset_done"))
                self.assertEqual(mail.outbox[-1].to, [user.email])
                self.assertNotIn(self.password, mail.outbox[-1].body)
                self.assertIn("одну годину", mail.outbox[-1].body)
                link = re.search(r'https?://[^\s]+/reset/[^\s]+', mail.outbox[-1].body).group(0)
                path = link.split("testserver", 1)[-1]
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("set-password", response.url)
                self.assertEqual(response["Referrer-Policy"], "no-referrer")
                self.assertRedirects(self.client.post(response.url, {"new_password1": self.new_password, "new_password2": self.new_password}), reverse("password_reset_complete"))
                user.refresh_from_db()
                self.assertTrue(user.check_password(self.new_password))
                self.assertFalse(user.check_password(self.password))
                self.assertFalse(self.client.get(path).context["validlink"])
                self.assertTrue(self.client.login(username=user.username, password=self.new_password))
                self.client.logout()

    def test_unknown_unverified_and_duplicate_addresses_return_same_page_without_mail(self):
        unverified = self.user(verified=False)
        duplicate = self.user(username="duplicated")
        self.user(username="other_owner", email=duplicate.email.upper(), verified=False)
        for email in ("missing@example.invalid", unverified.email, duplicate.email):
            response = self.request_reset(email)
            self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_unicode_equivalent_duplicate_address_is_ambiguous(self):
        user = self.user(email="owner@example.invalid")
        self.user(username="unicode_owner", email="ｏｗｎｅｒ@example.invalid", verified=False)
        self.assertIsNone(verified_account(user.email))
        self.request_reset(user.email)
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_and_unusable_password_accounts_do_not_receive_reset(self):
        user = self.user()
        user.is_active = False
        user.save()
        self.request_reset(user.email)
        user.is_active = True
        user.set_unusable_password()
        user.save()
        self.request_reset(user.email)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_rate_limit_is_per_email_and_ip_and_has_generic_response(self):
        user = self.user()
        for _ in range(6):
            self.assertRedirects(self.request_reset(user.email), reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 3)
        second = self.user(username="second_reset")
        self.assertRedirects(self.request_reset(second.email), reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 3)

    def test_reset_token_expires_and_invalid_token_is_rejected(self):
        user = self.user()
        with patch.object(reset_tokens, "_now", return_value=datetime(2026, 1, 1, 12)):
            url = self.reset_url(user)
        with patch.object(reset_tokens, "_now", return_value=datetime(2026, 1, 1, 14)):
            self.assertFalse(self.client.get(url).context["validlink"])
        invalid_url = reverse("password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(user.pk)), "invalid-token"])
        self.assertFalse(self.client.get(invalid_url).context["validlink"])

    def test_verification_revocation_or_email_change_invalidates_reset(self):
        user = self.user()
        url = self.reset_url(user)
        user.email = "changed@example.invalid"
        user.save(update_fields=["email"])
        self.assertIsNone(user.verified_email)
        self.assertFalse(self.client.get(url).context["validlink"])

    def test_profile_requires_current_password_and_keeps_old_email_until_confirmation(self):
        user = self.user()
        old = user.email
        response = self.profile_request(user, "new@example.invalid", password="incorrect")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.profile_request(user, "new@example.invalid").status_code, 302)
        user.refresh_from_db()
        self.assertEqual((user.email, user.verified_email), (old, old))
        token = self.email_token()
        self.assertNotEqual(EmailConfirmation.objects.get(user=user).token_digest, token)
        url = reverse("confirm_email", args=[token])
        self.assertEqual(self.client.get(url).status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, old)
        self.assertRedirects(self.client.post(url), reverse("profile"))
        user.refresh_from_db()
        self.assertEqual((user.email, user.verified_email), ("new@example.invalid", "new@example.invalid"))
        self.assertFalse(EmailConfirmation.objects.filter(user=user).exists())
        self.assertContains(self.client.post(url), "Посилання недійсне")

    def test_other_user_cannot_confirm_or_change_another_accounts_email(self):
        user = self.user()
        self.profile_request(user, "replacement@example.invalid")
        token = self.email_token()
        other = self.user(username="another_student")
        self.client.force_login(other)
        self.assertContains(self.client.post(reverse("confirm_email", args=[token])), "Посилання недійсне")
        user.refresh_from_db()
        self.assertNotEqual(user.email, "replacement@example.invalid")

    def test_repeated_email_confirmation_invalidates_previous_link(self):
        user = self.user(verified=False)
        self.profile_request(user)
        old = self.email_token()
        self.profile_request(user)
        latest = self.email_token()
        self.assertNotEqual(old, latest)
        self.assertContains(self.client.post(reverse("confirm_email", args=[old])), "Посилання недійсне")
        self.assertRedirects(self.client.post(reverse("confirm_email", args=[latest])), reverse("profile"))

    def test_confirmation_expires_and_password_change_invalidates_pending_link(self):
        user = self.user(verified=False)
        self.profile_request(user)
        token = self.email_token()
        EmailConfirmation.objects.filter(user=user).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertContains(self.client.post(reverse("confirm_email", args=[token])), "Посилання недійсне")
        self.profile_request(user)
        token = self.email_token()
        user.set_password(self.new_password)
        user.save(update_fields=["password"])
        self.client.force_login(user)
        self.assertContains(self.client.post(reverse("confirm_email", args=[token])), "Посилання недійсне")

    def test_duplicate_email_change_does_not_send_or_reassign_ownership(self):
        user = self.user()
        other = self.user(username="existing_owner")
        self.profile_request(user, other.email)
        self.assertEqual(len(mail.outbox), 0)
        user.refresh_from_db()
        self.assertNotEqual(user.email, other.email)

    def test_confirmation_requests_are_limited(self):
        user = self.user(verified=False)
        for _ in range(8):
            self.profile_request(user)
        self.assertEqual(len(mail.outbox), 5)

    def test_authenticated_password_change_requires_old_password_and_keeps_session(self):
        user = self.user()
        self.client.force_login(user)
        response = self.client.post(reverse("password_change"), {"old_password": "incorrect", "new_password1": self.new_password, "new_password2": self.new_password})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertRedirects(self.client.post(reverse("password_change"), {"old_password": self.password, "new_password1": self.new_password, "new_password2": self.new_password}), reverse("profile"))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.new_password))

    def test_password_rules_and_confirmation_must_match(self):
        user = self.user()
        response = self.client.get(self.reset_url(user))
        for passwords in [("123", "123"), (self.new_password, "different")]:
            response2 = self.client.post(response.url, {"new_password1": passwords[0], "new_password2": passwords[1]})
            self.assertEqual(response2.status_code, 200)
            self.assertTrue(response2.context["form"].errors)
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.password))

    def test_profile_and_email_confirmation_require_login_and_csrf(self):
        self.assertEqual(self.client.get(reverse("profile")).status_code, 302)
        user = self.user()
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        self.assertEqual(client.post(reverse("profile"), {"email": "new@example.invalid", "current_password": self.password}).status_code, 403)
        self.assertEqual(client.post(reverse("password_change"), {"old_password": self.password}).status_code, 403)
        self.assertEqual(client.post(reverse("password_reset"), {"email": user.email}).status_code, 403)
        self.assertEqual(client.post(reverse("confirm_email", args=["invalid-token"])).status_code, 403)

    def test_new_registration_is_unverified_and_duplicate_email_is_rejected(self):
        self.assertContains(self.client.get(reverse("password_reset")), "Електронна пошта")
        data = {"username": "new_account", "email": "NewAccount@example.invalid", "password1": self.password, "password2": self.password}
        self.assertEqual(self.client.post(reverse("register"), data).status_code, 302)
        user = User.objects.get(username="new_account")
        self.assertEqual(user.email, "newaccount@example.invalid")
        self.assertIsNone(user.verified_email)
        self.assertEqual(len(mail.outbox), 1)
        self.client.logout()
        response = self.client.post(reverse("register"), {**data, "username": "duplicate_account"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
