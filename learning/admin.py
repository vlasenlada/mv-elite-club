
from django.contrib import admin

from .models import Assignment, Submission


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "lesson",
        "due_at",
        "created_at",
    )

    list_filter = (
        "due_at",
        "created_at",
    )

    search_fields = (
        "title",
        "description",
    )

    readonly_fields = ("created_at",)

    ordering = ("-created_at",)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "assignment",
        "student",
        "grade",
        "submitted_at",
    )

    list_filter = (
        "submitted_at",
    )

    search_fields = (
        "assignment__title",
        "student__username",
    )

    readonly_fields = ("submitted_at",)

    ordering = ("-submitted_at",)
