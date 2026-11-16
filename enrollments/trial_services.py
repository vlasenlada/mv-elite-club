from django.db import connections
from django.db.models import Q

from .trial_models import TrialRequest, TrialGroupAdmission


def trial_storage_ready():
    tables = set(connections["default"].introspection.table_names())
    return {TrialRequest._meta.db_table, TrialGroupAdmission._meta.db_table} <= tables


def duplicate_trial(trial):
    return TrialRequest.objects.filter(course=trial.course, student_key=trial.student_key, status__in=TrialRequest.ACTIVE).filter(
        Q(email_key=trial.email_key) | Q(phone_key=trial.phone_key)).exclude(pk=trial.pk).exists()


def accessible_trials(user):
    if not user.is_authenticated or not user.is_active or user.is_staff:
        return TrialRequest.objects.none()
    if user.role == "teacher":
        return TrialRequest.objects.filter(Q(teacher=user) | Q(user=user))
    if user.role == "student":
        return TrialRequest.objects.filter(user=user)
    return TrialRequest.objects.none()
