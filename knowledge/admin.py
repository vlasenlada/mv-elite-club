from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Avg, Count, Max, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .models import Assessment, CourseRecommendation, Option, Passage, Question, ResultBand
from .admin_forms import (AssessmentAdminForm, BandAdminForm, BandFormSet, OptionAdminForm,
                          OptionFormSet, PassageAdminForm, QuestionAdminForm, RecommendationAdminForm)
from .admin_readiness import publication_readiness
from .services import storage_ready


def administrator(user):
    return user.is_active and user.is_staff and (user.is_superuser or user.role == "admin")


class KnowledgeAdmin(admin.ModelAdmin):
    change_form_template = "admin/knowledge/related_change_form.html"

    class Media:
        css = {"all": ("css/knowledge-admin.css",)}

    def get_model_perms(self, request):
        return super().get_model_perms(request) if storage_ready() and administrator(request.user) else {}

    def has_module_permission(self, request):
        return administrator(request.user) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return administrator(request.user) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return administrator(request.user) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return administrator(request.user) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return administrator(request.user) and super().has_delete_permission(request, obj)

    def changelist_view(self, request, extra_context=None):
        if not storage_ready():
            return HttpResponse("Модуль очікує застосування міграції.", status=503)
        return super().changelist_view(request, extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if not storage_ready():
            return HttpResponse("Модуль очікує застосування міграції.", status=503)
        context = dict(extra_context or {})
        obj = self.get_object(request, object_id) if object_id else None
        assessment = obj.assessment if obj and hasattr(obj, "assessment") else None
        if assessment is None:
            value = request.POST.get("assessment") if request.method == "POST" else request.GET.get("assessment")
            try:
                assessment = Assessment.objects.filter(pk=int(value)).first()
            except (TypeError, ValueError, OverflowError):
                pass
        context["parent_assessment"] = assessment
        if assessment:
            context["parent_assessment_url"] = reverse("admin:knowledge_assessment_change", args=[assessment.pk])
            if self.model is Question and assessment.subject == "english":
                context["reading_add_url"] = reverse("admin:knowledge_passage_add") + f"?assessment={assessment.pk}"
                context["reading_list_url"] = reverse("admin:knowledge_passage_changelist") + f"?assessment__id__exact={assessment.pk}"
                context["next_question_url"] = reverse("admin:knowledge_question_add") + f"?assessment={assessment.pk}"
        return super().changeform_view(request, object_id, form_url, context)

    def response_add(self, request, obj, post_url_continue=None):
        if not any(key in request.POST for key in ("_continue", "_addanother", "_popup")) and hasattr(obj, "assessment_id"):
            self.message_user(request, "Збережено. Ви повернулися до тесту.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:knowledge_assessment_change", args=[obj.assessment_id]) + "#knowledge-questions")
        if "_addanother" in request.POST and hasattr(obj, "assessment_id") and "_popup" not in request.POST:
            self.message_user(request, "Збережено. Можна додати наступний запис до цього тесту.", messages.SUCCESS)
            return HttpResponseRedirect(reverse(f"admin:knowledge_{self.model._meta.model_name}_add") + f"?assessment={obj.assessment_id}")
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if not any(key in request.POST for key in ("_continue", "_addanother", "_popup")) and hasattr(obj, "assessment_id"):
            self.message_user(request, "Зміни збережено. Якщо навчальний зміст змінено, тест очікує повторної перевірки й публікації.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:knowledge_assessment_change", args=[obj.assessment_id]) + "#knowledge-questions")
        return super().response_change(request, obj)

    def delete_view(self, request, object_id, extra_context=None):
        obj = self.get_object(request, object_id)
        request.knowledge_parent_id = getattr(obj, "assessment_id", None)
        return super().delete_view(request, object_id, extra_context)

    def response_delete(self, request, obj_display, obj_id):
        parent_id = getattr(request, "knowledge_parent_id", None)
        if parent_id and not request.POST.get("_popup"):
            self.message_user(request, "Запис видалено. Перевірте готовність тесту перед повторною публікацією.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:knowledge_assessment_change", args=[parent_id]) + "#knowledge-questions")
        return super().response_delete(request, obj_display, obj_id)


class OptionInline(admin.TabularInline):
    model = Option
    form = OptionAdminForm
    formset = OptionFormSet
    extra = 2
    max_num = 10
    fields = ("order", "text", "is_correct")


class BandInline(admin.StackedInline):
    model = ResultBand
    form = BandAdminForm
    formset = BandFormSet
    extra = 0
    fields = ("minimum_percent", "title", "feedback", "level_hint")


class RecommendationInline(admin.StackedInline):
    model = CourseRecommendation
    form = RecommendationAdminForm
    extra = 0
    fields = ("course", ("minimum_percent", "maximum_percent"), "explanation", "order")

    def get_formset(self, request, obj=None, **kwargs):
        base = super().get_formset(request, obj, **kwargs)

        class ContextFormSet(base):
            def get_form_kwargs(self, index):
                result = super().get_form_kwargs(index)
                result["assessment"] = self.instance
                return result

        return ContextFormSet


@admin.register(Assessment)
class AssessmentAdmin(KnowledgeAdmin):
    form = AssessmentAdminForm
    change_form_template = "admin/knowledge/assessment/change_form.html"
    change_list_template = "admin/knowledge/assessment/change_list.html"
    list_display = ("title", "subject", "grade_band", "grade", "question_count", "publication_status", "finished_count", "average_score")
    list_filter = ("subject", "category", "grade_band", "grade", "is_published", "level_estimation_enabled")
    search_fields = ("title", "description", "slug")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("question_links", "publication_status", "finished_count", "average_score", "updated_at")
    inlines = (BandInline, RecommendationInline)
    actions = ("check_selected", "publish_selected", "hide_selected")
    fieldsets = (
        ("1. Основна інформація", {"fields": ("title", "description", ("subject", "category"), ("estimated_minutes", "question_count"))}),
        ("2. Для кого цей тест", {"fields": ("grade_band", "grade", "audience")}),
        ("3. Запитання й тексти для читання", {"fields": ("question_links",)}),
        ("4. Публікація", {"fields": ("publication_status",), "description": "Спочатку збережіть чернетку й додайте запитання. Результати та рекомендації редагуються нижче. Кнопки перевірки й публікації зберігають усі зміни цієї сторінки."}),
        ("Додатково: адреса й пошукові системи", {"fields": ("slug", "seo_title", "seo_description"), "classes": ("collapse",)}),
        ("Додатково: методика та авторство", {"fields": ("level_estimation_enabled", "methodology_notes", "source_notes"), "classes": ("collapse",)}),
        ("Агрегована статистика", {"fields": ("finished_count", "average_score", "updated_at"), "classes": ("collapse",)}),
    )

    def get_prepopulated_fields(self, request, obj=None):
        return {} if obj else self.prepopulated_fields

    def view_on_site(self, obj):
        return obj.get_absolute_url() if obj.is_published else None

    @admin.display(description="Статус")
    def publication_status(self, obj):
        return "Опубліковано" if obj and obj.is_published else "Чернетка — не видно відвідувачам"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        context = dict(extra_context or {})
        obj = self.get_object(request, object_id) if object_id and storage_ready() else None
        context["knowledge_readiness"] = publication_readiness(obj) if obj else None
        return super().changeform_view(request, object_id, form_url, context)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _finished=Count("attempts", filter=Q(attempts__completed_at__isnull=False)), _average=Avg("attempts__percentage"))

    @admin.display(description="Завершені спроби")
    def finished_count(self, obj):
        return getattr(obj, "_finished", 0)

    @admin.display(description="Середній результат")
    def average_score(self, obj):
        value = getattr(obj, "_average", None)
        return f"{value:.2f}%" if value is not None else "Ще немає завершених спроб"

    @admin.display(description="Запитання")
    def question_links(self, obj):
        if not obj or not obj.pk:
            return "Збережіть чернетку, потім додайте запитання та варіанти відповідей."
        return render_to_string("admin/knowledge/assessment/questions.html", {
            "test": obj, "questions": obj.questions.select_related("passage").order_by("order", "pk"),
            "passages": obj.passages.all(),
        })

    def save_model(self, request, obj, form, change):
        # The admin change form is atomic. Publish only after its inlines are saved.
        obj.is_published = False
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        test = form.instance
        content_fields = {"subject", "category", "grade_band", "grade", "audience", "question_count", "level_estimation_enabled", "methodology_notes"}
        content_changed = bool(content_fields.intersection(form.changed_data)) or any(formset.has_changed() for formset in formsets)
        should_publish = "_publish" in request.POST or (form.was_published and not content_changed and "_hide" not in request.POST)
        status = publication_readiness(test)
        if should_publish and status["ready"]:
            test.is_published = True
            try:
                test.save(update_fields=["is_published", "updated_at"])
            except ValidationError as error:
                test.is_published = False
                self.message_user(request, "; ".join(error.messages), messages.ERROR)
        elif should_publish:
            self.message_user(request, "Тест збережено як чернетку. Публікація неможлива — виправте помилки в блоці перевірки готовності.", messages.ERROR)
        if "_check" in request.POST:
            self.message_user(request, "Перевірку завершено: тест готовий до публікації." if status["ready"] else "Перевірку завершено: є помилки. Перелік із посиланнями — на початку сторінки.", messages.SUCCESS if status["ready"] else messages.WARNING)

    def response_add(self, request, obj, post_url_continue=None):
        if "_popup" not in request.POST and "_addanother" not in request.POST:
            self.message_user(request, "Тест збережено. Додайте запитання, налаштуйте результати й перевірте готовність.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:knowledge_assessment_change", args=[obj.pk]) + "#knowledge-questions")
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if any(key in request.POST for key in ("_check", "_publish", "_hide")):
            if "_publish" in request.POST and obj.is_published:
                self.message_user(request, "Тест опубліковано. Його можна відкрити на сайті.", messages.SUCCESS)
            if "_hide" in request.POST:
                self.message_user(request, "Тест приховано. Історичні результати збережені.", messages.SUCCESS)
            return HttpResponseRedirect(reverse("admin:knowledge_assessment_change", args=[obj.pk]))
        return super().response_change(request, obj)

    @admin.action(description="Перевірити готовність обраних тестів", permissions=["change"])
    def check_selected(self, request, queryset):
        for test in queryset:
            status = publication_readiness(test)
            text = "Готовий до публікації." if status["ready"] else " ".join(issue["message"] for issue in status["issues"])
            self.message_user(request, f"«{test.title}»: {text}", messages.SUCCESS if status["ready"] else messages.WARNING)

    @admin.action(description="Перевірити й опублікувати обрані тести", permissions=["change"])
    def publish_selected(self, request, queryset):
        for test in queryset:
            status = publication_readiness(test)
            if not status["ready"]:
                self.message_user(request, f"«{test.title}»: " + " ".join(issue["message"] for issue in status["issues"]), messages.ERROR)
                continue
            test.is_published = True
            try:
                test.save()
            except ValidationError as error:
                self.message_user(request, f"Тест №{test.pk}: {'; '.join(error.messages)}", messages.ERROR)
            else:
                self.message_user(request, f"Тест №{test.pk} опубліковано.", messages.SUCCESS)

    @admin.action(description="Приховати обрані тести", permissions=["change"])
    def hide_selected(self, request, queryset):
        queryset.update(is_published=False, updated_at=timezone.now())

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.attempts.exists())


@admin.register(Question)
class QuestionAdmin(KnowledgeAdmin):
    form = QuestionAdminForm
    list_display = ("assessment", "order", "question_preview", "topic", "kind", "difficulty", "is_active")
    list_filter = ("assessment", "assessment__subject", "topic", "kind", "difficulty", "is_active")
    search_fields = ("prompt", "topic", "assessment__title")
    list_editable = ("order", "is_active")
    inlines = (OptionInline,)
    fieldsets = (
        ("Запитання", {"fields": ("assessment", ("order", "is_active"), "prompt", ("kind", "difficulty"), "topic")}),
        ("Текст для читання (англійська мова)", {"fields": ("passage",)}),
        ("Пояснення після завершення тесту", {"fields": ("explanation",)}),
        ("Для числової відповіді", {"fields": ("numeric_answer", "tolerance"), "description": "Для інших типів залиште число порожнім. Варіанти відповідей редагуються нижче; для числового завдання вони не потрібні."}),
    )

    @admin.display(description="Формулювання")
    def question_preview(self, obj):
        return obj.prompt[:100]

    def get_changeform_initial_data(self, request):
        data = super().get_changeform_initial_data(request)
        try:
            parent_id = int(request.GET.get("assessment", ""))
        except (ValueError, TypeError, OverflowError):
            return data
        data["order"] = (Question.objects.filter(assessment_id=parent_id).aggregate(last=Max("order"))["last"] or 0) + 1
        return data


@admin.register(Passage)
class PassageAdmin(KnowledgeAdmin):
    form = PassageAdminForm
    readonly_fields = ("used_by",)
    fields = ("assessment", "title", "text", "used_by")
    list_display = ("title", "assessment")
    list_filter = ("assessment",)
    search_fields = ("title", "text")

    @admin.display(description="Запитання, які використовують цей текст")
    def used_by(self, obj):
        if not obj or not obj.pk:
            return "Збережіть текст, потім оберіть його в запитаннях цього тесту."
        links = format_html_join(" · ", '<a href="{}">Запитання {}</a>', ((reverse("admin:knowledge_question_change", args=[q.pk]), q.order) for q in obj.questions.all()))
        return format_html('{}<p>Використаний текст не можна видалити. Спочатку відв’яжіть його від запитань.</p>', links) if obj.questions.exists() else "Ще не використовується."

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.questions.exists())

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(ResultBand)
class ResultBandAdmin(KnowledgeAdmin):
    form = BandAdminForm
    list_display = ("assessment", "minimum_percent", "title", "level_hint")
    list_filter = ("assessment", "level_hint")
    search_fields = ("title", "feedback")


@admin.register(CourseRecommendation)
class CourseRecommendationAdmin(KnowledgeAdmin):
    form = RecommendationAdminForm
    list_display = ("assessment", "course", "minimum_percent", "maximum_percent")
    list_filter = ("assessment", "course")
