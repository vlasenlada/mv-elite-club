
from django.contrib import admin
from .models import Course, CoursePackage


@admin.register(CoursePackage)
class CoursePackageAdmin(admin.ModelAdmin):
    list_display = ("course", "learning_format", "lesson_count", "price", "is_active")
    list_filter = ("course", "learning_format", "lesson_count")
    list_editable = ("price", "is_active")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        for learning_format in ("individual", "group"):
            for count in (4, 8, 12):
                CoursePackage.objects.get_or_create(course=form.instance, learning_format=learning_format, lesson_count=count)
    prepopulated_fields = {"slug": ("title",)}
    list_display = (
        "title",
        "subject",
        "grade_band",
        "supports_individual",
        "supports_group",
        "individual_price",
        "group_price",
        "is_published",
        "created_at",
    )

    list_filter = (
        "subject",
        "grade_band",
        "supports_individual",
        "supports_group",
        "is_published",
    )

    search_fields = (
        "title",
        "description",
    )

    list_editable = ("is_published",)
    readonly_fields = ("price", "lesson_format")

    def save_model(self, request, obj, form, change):
        if not obj.lesson_format:
            obj.lesson_format = "individual" if obj.supports_individual else "group"
        super().save_model(request, obj, form, change)
    fieldsets = (
        ("Курс", {"fields": ("title", "slug", "subject", "grade_band", "is_published")}),
        ("Зміст сторінки", {"fields": ("card_tone", "tagline", "description", "program", "benefits", "outcomes")}),
        ("Формати та вартість одного заняття", {
            "fields": ("supports_individual", "individual_price", "supports_group", "group_price"),
            "description": "Групова ціна вказується за одного учня. Порожні тарифи означають, що вартість уточнюється. Оплата й пакети не налаштовуються тут.",
        }),
        ("Збережені історичні дані", {"fields": ("price", "lesson_format"), "classes": ("collapse",),
                                    "description": "Попередня ціна зберігається без змін і не підставляється у нові тарифи автоматично."}),
    )
