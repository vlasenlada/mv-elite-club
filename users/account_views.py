from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView, PasswordResetConfirmView, PasswordChangeView
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from .account_security import (VerifiedPasswordResetForm, allow_reset, confirm_email,
    request_email_confirmation, reset_tokens, verified_account)


class SecurePasswordResetView(PasswordResetView):
    form_class = VerifiedPasswordResetForm
    token_generator = reset_tokens
    template_name = "users/password_reset_form.html"
    email_template_name = "users/password_reset_email.txt"
    subject_template_name = "users/password_reset_subject.txt"

    def form_valid(self, form):
        if allow_reset(self.request, form.cleaned_data["email"]):
            return super().form_valid(form)
        return redirect("password_reset_done")


class SecurePasswordResetConfirmView(PasswordResetConfirmView):
    token_generator = reset_tokens
    template_name = "users/password_reset_confirm.html"

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        return user if user and verified_account(user.email) == user else None

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response


class AccountPasswordChangeView(PasswordChangeView):
    template_name = "users/password_change.html"
    success_url = reverse_lazy("profile")

    def form_valid(self, form):
        messages.success(self.request, "Пароль успішно змінено.")
        return super().form_valid(form)


class EmailChangeForm(forms.Form):
    email = forms.EmailField(label="Електронна пошта для підтвердження")
    current_password = forms.CharField(label="Поточний пароль", widget=forms.PasswordInput, strip=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Неправильний поточний пароль.")
        return password


@login_required
@never_cache
@sensitive_post_parameters("current_password")
def profile(request):
    form = EmailChangeForm(request.POST if request.method == "POST" else None,
        user=request.user, initial={"email": request.user.email})
    if request.method == "POST" and form.is_valid():
        request_email_confirmation(request.user, form.cleaned_data["email"], request)
        messages.info(request, "Якщо адресу можна використати, ми надіслали інструкцію підтвердження. Перевірте пошту; повторні запити обмежені.")
        return redirect("profile")
    return render(request, "users/profile.html", {"form": form})


@login_required
@never_cache
def email_confirmation(request, token):
    valid = False
    if request.method == "POST":
        try:
            valid = confirm_email(request.user, token)
        except IntegrityError:
            valid = False
        if valid:
            messages.success(request, "Електронну пошту підтверджено. Відновлення пароля тепер доступне.")
            return redirect("profile")
    response = render(request, "users/email_confirmation.html", {"failed": request.method == "POST" and not valid})
    response["Referrer-Policy"] = "no-referrer"
    return response
