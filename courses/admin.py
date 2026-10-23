
from django.contrib import admin
from .models import Course


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "subject",
        "lesson_format",
        "price",
        "is_published",
        "created_at",
    )

    list_filter = (
        "subject",
        "lesson_format",
        "is_published",
    )

    search_fields = (
        "title",
        "description",
    )

    list_editable = ("is_published",)
