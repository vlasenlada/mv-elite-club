
from django.contrib import admin

from .models import Lesson
from enrollments.models import Enrollment


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):

    list_display = (
        "enrollment",
        "starts_at",
        "ends_at",
        "status",
    )

    list_filter = (
        "status",
        "starts_at",
    )

    search_fields = (
        "enrollment__student__username",
        "enrollment__course__title",
    )

    list_select_related = (
        "enrollment__student",
        "enrollment__course",
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "enrollment":
            kwargs["queryset"] = Enrollment.objects.filter(
                status=Enrollment.Status.APPROVED
            ).select_related("student", "course")

        return super().formfield_for_foreignkey(
            db_field,
            request,
            **kwargs
        )
