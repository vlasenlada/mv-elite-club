from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from enrollments.models import Enrollment
from enrollments.trial_models import TrialGroupAdmission
from enrollments.trial_services import trial_storage_ready
from users.teacher_assignment import eligible_teachers

from .models import Attendance, GroupMembership, StudyGroup


class ArchiveAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


class MembershipDates:
    @admin.display(description="Дата вступу")
    def join_date(self, obj):
        return obj.joined_at

    @admin.display(description="Дата завершення участі")
    def leave_date(self, obj):
        return obj.left_at


class StudyGroupAdminForm(forms.ModelForm):
    class Meta:
        model = StudyGroup
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["capacity"].required = not bool(self.instance.pk)


class MembershipInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(form.errors for form in self.forms):
            return
        projected = dict(self.instance.memberships.filter(is_active=True).values_list("pk", "enrollment_id")) if self.instance.pk else {}
        existing_ids = set()
        for index, form in enumerate(self.forms):
            data = form.cleaned_data
            if not data:
                continue
            key = form.instance.pk or f"new-{index}"
            if form.instance.pk and form.instance.pk in existing_ids:
                raise ValidationError("Один запис учасника не можна передавати двічі.")
            if form.instance.pk:
                existing_ids.add(form.instance.pk)
            projected.pop(key, None)
            if data.get("is_active") and data.get("enrollment"):
                projected[key] = data["enrollment"].pk
        if len(set(projected.values())) != len(projected):
            raise ValidationError("Одна заявка не може займати кілька місць у групі.")
        if self.instance.capacity is not None and len(projected) > self.instance.capacity:
            raise ValidationError("Кількість активних учасників перевищує місткість групи. Збільште ліміт або зменште список.")
        original_count = self.instance.member_count
        if self.instance.capacity is None and len(projected) > original_count:
            raise ValidationError("Перед додаванням нових учнів вкажіть місткість групи.")


class GroupMembershipAdminForm(forms.ModelForm):
    class Meta:
        model = GroupMembership
        fields = "__all__"

    def clean(self):
        data = super().clean()
        group = data.get("group")
        if not self.instance.pk and data.get("is_active") and group and group.capacity is None:
            raise ValidationError("Перед додаванням нових учнів вкажіть місткість групи.")
        return data


class MembershipInline(MembershipDates, admin.TabularInline):
    formset = MembershipInlineFormSet
    model = GroupMembership
    extra = 0
    can_delete = False
    readonly_fields = ("join_date", "leave_date")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "enrollment":
            kwargs["queryset"] = Enrollment.objects.filter(student__role="student", student__is_staff=False)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class TrialAdmissionInline(admin.StackedInline):
    model = TrialGroupAdmission
    extra = 1
    max_num = 1
    can_delete = False


@admin.register(StudyGroup)
class StudyGroupAdmin(ArchiveAdmin):
    form = StudyGroupAdminForm
    list_display = ("name", "course", "teacher", "capacity", "participants", "vacancies", "is_active")
    readonly_fields = ("participants", "vacancies")
    list_filter = ("course", "is_active")
    search_fields = ("name", "course__title")
    inlines = (MembershipInline, TrialAdmissionInline)

    def get_inline_instances(self, request, obj=None):
        inlines = super().get_inline_instances(request, obj)
        # Existing group POSTs without the new optional formset must keep working.
        # Omitting it leaves the trial policy unchanged (closed by default).
        if not trial_storage_ready() or (request.method == "POST" and "trial_admission-TOTAL_FORMS" not in request.POST):
            return [inline for inline in inlines if not isinstance(inline, TrialAdmissionInline)]
        return inlines

    @admin.display(description="Учасників")
    def participants(self, obj):
        return obj.member_count if obj else 0

    @admin.display(description="Вільних місць")
    def vacancies(self, obj):
        return obj.free_places if obj and obj.capacity is not None else "Ліміт не встановлено"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "teacher":
            kwargs["queryset"] = eligible_teachers()
        if db_field.name == "course":
            kwargs["queryset"] = db_field.related_model.objects.filter(supports_group=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(GroupMembership)
class GroupMembershipAdmin(MembershipDates, ArchiveAdmin):
    form = GroupMembershipAdminForm
    list_display = ("group", "enrollment", "is_active", "join_date", "leave_date")
    list_filter = ("group", "is_active")
    readonly_fields = ("join_date", "leave_date")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "enrollment":
            kwargs["queryset"] = Enrollment.objects.filter(student__role="student", student__is_staff=False)
        if db_field.name == "group":
            kwargs["queryset"] = StudyGroup.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Attendance)
class AttendanceAdmin(ArchiveAdmin):
    list_display = ("lesson", "membership", "status")
    list_filter = ("status", "lesson__group")

    def get_readonly_fields(self, request, obj=None):
        return ("lesson", "membership") if obj else ()
