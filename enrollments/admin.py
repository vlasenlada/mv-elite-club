
from django.contrib import admin
from django import forms
from django.db import transaction
from .models import Enrollment
from .services import transfer_enrollment_teacher
from users.teacher_assignment import eligible_teachers


class EnrollmentAdminForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["requested_format"].required = False
        if not self.instance.pk:
            self.instance.format_is_explicit = True

    def clean_requested_format(self):
        return self.cleaned_data.get("requested_format") or self.instance.requested_format or "individual"


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    form = EnrollmentAdminForm
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "teacher":
            kwargs["queryset"] = eligible_teachers()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if not change:
            obj.format_is_explicit = True
        if change and "teacher" in form.changed_data:
            teacher_id = obj.teacher_id
            # An administrator may approve and assign in the same form.
            obj.teacher_id = Enrollment.objects.get(pk=obj.pk).teacher_id
            super().save_model(request, obj, form, change)
            transfer_enrollment_teacher(obj.pk, teacher_id)
            obj.teacher_id = teacher_id
        else:
            super().save_model(request, obj, form, change)

    list_display = (
        "student",
        "course",
        "status",
        "requested_format",
        "created_at",
    )

    list_filter = (
        "status",
        "requested_format",
        "course",
        "created_at",
    )

    search_fields = (
        "student__username",
        "student__email",
        "course__title",
    )

    readonly_fields = (
        "created_at",
    )

    list_select_related = (
        "student",
        "course",
    )
