
from django.contrib import admin
from .models import Enrollment


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "course",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
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
