from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from courses.models import Course
from users.models import User
from .admin_forms import AssessmentAdminForm, QuestionAdminForm, suggested_slug
from .admin_readiness import publication_readiness
from .models import Assessment, Attempt, CourseRecommendation, Option, Passage, Question, ResultBand
from .templatetags.knowledge_admin import knowledge_first


class KnowledgeAdminWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_superuser(username="test-content-editor", role="admin", password="Admin-workflow-test-123")
        cls.test = Assessment.objects.create(title="Математика — 7 клас", slug="math-seven", subject="math", grade_band="middle", grade=7, question_count=3)
        cls.course = Course.objects.get(slug="math-middle")

    def setUp(self):
        self.client.force_login(self.manager)

    def assessment_data(self, test=None, **extra):
        test = test or self.test
        data = {"title": test.title, "slug": test.slug, "subject": test.subject, "category": test.category,
                "grade_band": test.grade_band, "grade": test.grade or "", "question_count": test.question_count,
                "estimated_minutes": test.estimated_minutes, "audience": test.audience,
                "result_bands-TOTAL_FORMS": 0, "result_bands-INITIAL_FORMS": 0,
                "recommendations-TOTAL_FORMS": 0, "recommendations-INITIAL_FORMS": 0, "_save": "1"}
        if test.level_estimation_enabled:
            data["level_estimation_enabled"] = "on"
        data.update(extra)
        return data

    def question_data(self, kind="single", test=None, **extra):
        data = {"assessment": (test or self.test).pk, "order": 1, "is_active": "on", "prompt": "Обчисли 2 + 2.",
                "topic": "Додавання", "difficulty": "easy", "kind": kind,
                "explanation": "Дві пари утворюють чотири одиниці.", "tolerance": "0",
                "options-TOTAL_FORMS": 0 if kind == "numeric" else 2, "options-INITIAL_FORMS": 0,
                "options-0-order": 1, "options-0-text": "4", "options-0-is_correct": "on",
                "options-1-order": 2, "options-1-text": "5", "_save": "1"}
        if kind == "numeric":
            data["numeric_answer"] = "4"
        if kind == "multiple":
            data["options-1-is_correct"] = "on"
        data.update(extra)
        return data

    def change_url(self, test=None):
        return reverse("admin:knowledge_assessment_change", args=[(test or self.test).pk])

    def make_valid(self):
        question = Question.objects.create(assessment=self.test, prompt="Чотири?", kind="numeric", numeric_answer=4, topic="Лічба", explanation="Чотири одиниці.")
        self.test.question_count = 1
        self.test.save()
        return question

    def test_tests_first_only_in_knowledge_navigation(self):
        source = [{"app_label": "other", "models": [{"object_name": "Z"}, {"object_name": "A"}]},
                  {"app_label": "knowledge", "models": [{"object_name": "Question"}, {"object_name": "Assessment"}, {"object_name": "Passage"}]}]
        result = knowledge_first(source)
        self.assertEqual(result[0], source[0])
        self.assertEqual(result[1]["models"][0]["object_name"], "Assessment")
        self.assertEqual(source[1]["models"][0]["object_name"], "Question")
        response = self.client.get(reverse("admin:index"))
        html = response.content.decode()
        self.assertLess(html.index('id="knowledge-assessment"'), html.index('id="knowledge-question"'))

    def test_new_draft_returns_to_test_and_generates_ukrainian_title_slug(self):
        data = self.assessment_data(title="Математика — 8 клас", slug="", grade=8)
        response = self.client.post(reverse("admin:knowledge_assessment_add"), data)
        test = Assessment.objects.get(title="Математика — 8 клас")
        self.assertRedirects(response, self.change_url(test) + "#knowledge-questions")
        self.assertEqual(test.slug, "matematyka-8-klas")
        self.assertFalse(test.is_published)
        self.assertIn("yi", suggested_slug("Їжак"))
        self.assertIn("g", suggested_slug("Ґанок"))

    def test_url_unique_manual_editable_and_rename_stable(self):
        data = self.assessment_data(title="Новий тест", slug=self.test.slug)
        response = self.client.post(reverse("admin:knowledge_assessment_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тест із такою адресою")
        self.make_valid()
        self.test.is_published = True
        self.test.save()
        data = self.assessment_data(title="Нова назва", _check="1")
        response = self.client.post(self.change_url(), data)
        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db()
        self.assertEqual(self.test.slug, "math-seven")
        self.assertTrue(self.test.is_published)
        data = self.assessment_data(slug="manual-url", _check="1")
        self.client.post(self.change_url(), data)
        self.test.refresh_from_db()
        self.assertEqual(self.test.slug, "manual-url")

    def test_blank_auto_url_does_not_overwrite_other_test(self):
        Assessment.objects.create(title="Existing", slug="matematyka-7-klas", subject="math")
        form = AssessmentAdminForm(data=self.assessment_data(slug=""))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "matematyka-7-klas-2")

    def test_conflicting_subject_category_and_class_errors(self):
        response = self.client.post(self.change_url(), self.assessment_data(category="reading"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Для математики оберіть")
        response = self.client.post(self.change_url(), self.assessment_data(grade=3))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Клас має відповідати")

    def test_test_page_has_contextual_question_and_readiness_navigation(self):
        question = self.make_valid()
        response = self.client.get(self.change_url())
        for text in ("Перевірка збереженої версії", "Додати запитання до цього тесту", "Змінити порядок і активність списком", "Зберегти й перевірити", "Зберегти й опублікувати", "Додатково: адреса"):
            self.assertContains(response, text)
        self.assertContains(response, reverse("admin:knowledge_question_change", args=[question.pk]))
        self.assertContains(response, reverse("admin:knowledge_question_delete", args=[question.pk]))
        self.assertNotContains(response, 'name="is_published"')

    def test_readiness_reports_all_broken_questions_and_count(self):
        for number in (1, 2):
            Question.objects.create(assessment=self.test, order=number, topic="Алгебра")
        status = publication_readiness(self.test)
        self.assertFalse(status["ready"])
        self.assertEqual(len(status["issues"]), 3)
        response = self.client.post(self.change_url(), self.assessment_data(_publish="1"), follow=True)
        self.assertContains(response, "Публікація неможлива")
        self.assertContains(response, "Потрібно 3 активних запитань")
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_published)

    def test_single_option_validation_blocks_zero_or_two_correct_without_saving(self):
        for updates in ({"options-0-is_correct": ""}, {"options-1-is_correct": "on"}):
            response = self.client.post(reverse("admin:knowledge_question_add"), self.question_data(**updates))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "рівно один правильний")
        self.assertEqual(Question.objects.count(), 0)
        self.assertEqual(Option.objects.count(), 0)

    def test_multiple_options_and_duplicate_text_validation(self):
        data = self.question_data(kind="multiple", **{"options-1-is_correct": ""})
        response = self.client.post(reverse("admin:knowledge_question_add"), data)
        self.assertContains(response, "щонайменше два")
        data = self.question_data(**{"options-1-text": "4"})
        response = self.client.post(reverse("admin:knowledge_question_add"), data)
        self.assertContains(response, "не повинні повторюватися")
        response = self.client.post(reverse("admin:knowledge_question_add"), self.question_data(kind="multiple"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Question.objects.get().options.filter(is_correct=True).count(), 2)

    def test_numeric_comma_minus_tolerance_and_no_options(self):
        data = self.question_data(kind="numeric", numeric_answer="−2,5", tolerance="0,01")
        response = self.client.post(reverse("admin:knowledge_question_add"), data)
        self.assertRedirects(response, self.change_url() + "#knowledge-questions")
        question = Question.objects.get()
        self.assertEqual(question.numeric_answer, Decimal("-2.5"))
        self.assertEqual(question.tolerance, Decimal("0.01"))
        for values, message in (({"numeric_answer": ""}, "вкажіть правильне число"), ({"tolerance": "-1"}, "Похибка має бути"), ({"numeric_answer": "1/2"}, "")):
            response = self.client.post(reverse("admin:knowledge_question_add"), self.question_data(kind="numeric", **values))
            self.assertEqual(response.status_code, 200)
            if message:
                self.assertContains(response, message)
        data = self.question_data(kind="numeric", **{"options-TOTAL_FORMS": 2})
        response = self.client.post(reverse("admin:knowledge_question_add"), data)
        self.assertContains(response, "не має варіантів")
        self.assertEqual(Question.objects.count(), 1)

    def test_existing_decimal_values_render_without_exponents_and_can_be_saved_again(self):
        self.client.post(reverse("admin:knowledge_question_add"), self.question_data())
        question = Question.objects.get()
        response = self.client.get(reverse("admin:knowledge_question_change", args=[question.pk]))
        form = response.context["adminform"].form
        self.assertEqual(form["tolerance"].value(), "0")
        options = list(question.options.order_by("order"))
        data = self.question_data(tolerance=form["tolerance"].value(), **{
            "options-INITIAL_FORMS": 2, "options-0-id": options[0].pk, "options-1-id": options[1].pk})
        self.assertEqual(self.client.post(reverse("admin:knowledge_question_change", args=[question.pk]), data).status_code, 302)

    def test_missing_prompt_explanation_and_order_are_form_errors(self):
        response = self.client.post(reverse("admin:knowledge_question_add"), self.question_data(prompt="", explanation="", order=0))
        self.assertEqual(response.status_code, 200)
        form = response.context["adminform"].form
        self.assertIn("prompt", form.errors)
        self.assertIn("explanation", form.errors)
        self.assertIn("order", form.errors)
        self.assertEqual(Question.objects.count(), 0)

    def test_question_add_another_preserves_parent_and_next_order(self):
        response = self.client.post(reverse("admin:knowledge_question_add"), self.question_data(order=5, _addanother="1"))
        target = reverse("admin:knowledge_question_add") + f"?assessment={self.test.pk}"
        self.assertRedirects(response, target)
        response = self.client.get(target)
        form = response.context["adminform"].form
        self.assertEqual(str(form.initial["assessment"]), str(self.test.pk))
        self.assertEqual(form.initial["order"], 6)

    def test_reading_context_filtered_popup_and_guarded_deletion(self):
        english = Assessment.objects.create(title="Читання", slug="read", subject="english", question_count=1)
        other = Assessment.objects.create(title="Інший", slug="other-read", subject="english")
        passage = Passage.objects.create(assessment=english, text="An original short text.")
        alien = Passage.objects.create(assessment=other, text="Another text.")
        question = Question.objects.create(assessment=english, prompt="Read?", topic="Читання", passage=passage, explanation="Explain")
        response = self.client.get(reverse("admin:knowledge_question_change", args=[question.pk]))
        field = response.context["adminform"].form.fields["passage"]
        self.assertEqual(list(field.queryset), [passage])
        self.assertContains(response, f"assessment={english.pk}")
        response = self.client.post(reverse("admin:knowledge_question_add"), self.question_data(test=english, passage=alien.pk))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("admin:knowledge_passage_change", args=[passage.pk]))
        self.assertContains(response, "не можна видалити")
        self.assertContains(response, reverse("admin:knowledge_question_change", args=[question.pk]))
        delete = reverse("admin:knowledge_passage_delete", args=[passage.pk])
        self.assertEqual(self.client.get(delete).status_code, 403)
        self.assertEqual(self.client.post(delete, {"post": "yes"}).status_code, 403)
        self.assertTrue(Passage.objects.filter(pk=passage.pk).exists())
        self.assertNotIn("delete_selected", admin.site._registry[Passage].get_actions(self.client_request()))

    def client_request(self):
        from django.test import RequestFactory
        request = RequestFactory().get("/admin/")
        request.user = self.manager
        return request

    def test_passage_cannot_move_away_from_questions(self):
        english = Assessment.objects.create(title="Reading", slug="reading", subject="english")
        other = Assessment.objects.create(title="Other", slug="reading-other", subject="english")
        passage = Passage.objects.create(assessment=english, text="Original text")
        Question.objects.create(assessment=english, prompt="Read", topic="Reading", passage=passage)
        response = self.client.post(reverse("admin:knowledge_passage_change", args=[passage.pk]), {"assessment": other.pk, "text": passage.text, "_save": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Спочатку змініть прив’язку")
        passage.refresh_from_db()
        self.assertEqual(passage.assessment_id, english.pk)

    def test_reading_popup_uses_standard_django_response_and_math_is_rejected(self):
        english = Assessment.objects.create(title="Reading", slug="popup-reading", subject="english")
        response = self.client.post(reverse("admin:knowledge_passage_add") + f"?assessment={english.pk}&_popup=1", {
            "assessment": english.pk, "title": "Авторський текст", "text": "A short original text.", "_popup": "1", "_save": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-popup-response")
        self.assertEqual(Passage.objects.get().assessment_id, english.pk)
        response = self.client.post(reverse("admin:knowledge_passage_add"), {"assessment": self.test.pk, "text": "Text", "_save": "1"})
        self.assertContains(response, "Тексти для читання додаються")
        self.assertEqual(Passage.objects.count(), 1)

    def test_admin_publish_button_is_csrf_protected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.manager)
        response = csrf_client.post(self.change_url(), self.assessment_data(_publish="1"))
        self.assertEqual(response.status_code, 403)
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_published)

    def test_question_deletion_requires_confirmation_and_returns_to_test(self):
        question = self.make_valid()
        url = reverse("admin:knowledge_question_delete", args=[question.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Question.objects.filter(pk=question.pk).exists())
        response = self.client.post(url, {"post": "yes"})
        self.assertRedirects(response, self.change_url() + "#knowledge-questions")
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())

    def test_band_inline_errors_do_not_save_partial_changes(self):
        data = self.assessment_data(title="Не зберігати", **{"result_bands-TOTAL_FORMS": 1,
            "result_bands-0-minimum_percent": 20, "result_bands-0-title": "Результат", "result_bands-0-feedback": "Повтори тему"})
        response = self.client.post(self.change_url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "починатися з 0%")
        self.test.refresh_from_db()
        self.assertEqual(self.test.title, "Математика — 7 клас")
        self.assertFalse(ResultBand.objects.exists())

    def test_recommendations_offer_only_matching_courses_and_reject_direct_post(self):
        wrong = Course.objects.get(slug="english-middle")
        response = self.client.get(self.change_url())
        field = response.context["inline_admin_formsets"][1].formset.empty_form.fields["course"]
        self.assertIn(self.course, field.queryset)
        self.assertNotIn(wrong, field.queryset)
        self.assertNotContains(response, "add_id_recommendations-0-course")
        self.assertNotContains(response, "delete_id_recommendations-0-course")
        data = self.assessment_data(**{"recommendations-TOTAL_FORMS": 1,
            "recommendations-0-course": wrong.pk, "recommendations-0-minimum_percent": 0,
            "recommendations-0-maximum_percent": 100, "recommendations-0-order": 1})
        response = self.client.post(self.change_url(), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CourseRecommendation.objects.exists())

    def test_full_admin_workflow_scoring_and_frozen_history(self):
        course_before = list(Course.objects.values())
        for number, kind in enumerate(("single", "multiple", "numeric"), 1):
            response = self.client.post(reverse("admin:knowledge_question_add"), self.question_data(kind=kind, order=number))
            self.assertEqual(response.status_code, 302)
        data = self.assessment_data(_check="1", **{
            "result_bands-TOTAL_FORMS": 1, "result_bands-0-minimum_percent": 0,
            "result_bands-0-title": "Наступні кроки", "result_bands-0-feedback": "Повтори додавання.",
            "recommendations-TOTAL_FORMS": 1, "recommendations-0-course": self.course.pk,
            "recommendations-0-minimum_percent": 0, "recommendations-0-maximum_percent": 100,
            "recommendations-0-explanation": "Курс відповідає 7 класу.", "recommendations-0-order": 1})
        response = self.client.post(self.change_url(), data, follow=True)
        self.assertContains(response, "готовий до публікації")
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_published)
        band, rule = ResultBand.objects.get(), CourseRecommendation.objects.get()
        data.update({"_publish": "1", "result_bands-INITIAL_FORMS": 1, "result_bands-0-id": band.pk,
                     "recommendations-INITIAL_FORMS": 1, "recommendations-0-id": rule.pk})
        response = self.client.post(self.change_url(), data, follow=True)
        self.test.refresh_from_db()
        self.assertTrue(self.test.is_published, response.content.decode()[:1000])
        guest = Client()
        self.assertEqual(guest.get(self.test.get_absolute_url()).status_code, 200)
        start = guest.post(reverse("knowledge:start", args=["math", self.test.slug]))
        self.assertEqual(start.status_code, 302)
        attempt = Attempt.objects.get()
        for number, q in enumerate(self.test.questions.order_by("order"), 1):
            answer = "4,0" if q.kind == "numeric" else [str(pk) for pk in q.options.filter(is_correct=True).values_list("pk", flat=True)]
            response = guest.post(reverse("knowledge:question", args=[attempt.pk, number]), {"answer": answer, "move": "next"})
            self.assertEqual(response.status_code, 302)
        guest.post(reverse("knowledge:summary", args=[attempt.pk]))
        attempt.refresh_from_db()
        self.assertEqual(attempt.result["correct"], 3)
        self.assertEqual(attempt.percentage, 100)
        old_result, old_snapshot = attempt.result, attempt.snapshot
        first = self.test.questions.order_by("order").first()
        old_options = list(first.options.order_by("order"))
        changed = self.question_data(prompt="Оновлене запитання", **{
            "options-INITIAL_FORMS": 2, "options-0-id": old_options[0].pk,
            "options-1-id": old_options[1].pk})
        response = self.client.post(reverse("admin:knowledge_question_change", args=[first.pk]), changed)
        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_published)
        attempt.refresh_from_db()
        self.assertEqual(attempt.result, old_result)
        self.assertEqual(attempt.snapshot, old_snapshot)
        response = guest.get(reverse("knowledge:result", args=[attempt.pk]))
        self.assertContains(response, "Обчисли 2 + 2.")
        self.assertNotContains(response, "Оновлене запитання")
        self.assertEqual(list(Course.objects.values()), course_before)

    def test_unchanged_save_keeps_publication_and_content_changes_require_republication(self):
        self.make_valid()
        self.test.is_published = True
        self.test.save()
        response = self.client.post(self.change_url(), self.assessment_data(_check="1"))
        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db()
        self.assertTrue(self.test.is_published)
        response = self.client.post(self.change_url(), self.assessment_data(audience="Нова аудиторія", _check="1"))
        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_published)

    def test_hidden_and_invalid_publication_direct_post_permissions(self):
        self.make_valid()
        self.client.post(self.change_url(), self.assessment_data(_publish="1"))
        self.test.refresh_from_db()
        self.assertTrue(self.test.is_published)
        self.client.post(self.change_url(), self.assessment_data(_hide="1"))
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_published)
        teacher = User.objects.create_user(username="no-publishing", role="teacher", is_staff=True)
        teacher.user_permissions.add(*Permission.objects.filter(content_type__app_label="knowledge"))
        self.client.force_login(teacher)
        self.assertEqual(self.client.post(self.change_url(), self.assessment_data(_publish="1")).status_code, 403)

    def test_long_question_list_shows_all_thirty_questions(self):
        for number in range(1, 31):
            Question.objects.create(assessment=self.test, order=number, prompt=f"Завдання номер {number}", kind="numeric", numeric_answer=number, topic="Лічба", explanation="Лічимо одиниці.")
        response = self.client.get(self.change_url())
        self.assertContains(response, "Завдання номер 30")
        self.assertContains(response, "knowledge-table-scroll")

    def test_filtered_question_list_saves_order_and_requires_republication(self):
        question = self.make_valid()
        self.test.is_published = True
        self.test.save()
        url = reverse("admin:knowledge_question_changelist") + f"?assessment__id__exact={self.test.pk}"
        response = self.client.post(url, {"form-TOTAL_FORMS": 1, "form-INITIAL_FORMS": 1,
            "form-0-id": question.pk, "form-0-order": 10, "form-0-is_active": "on", "_save": "1"})
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.test.refresh_from_db()
        self.assertEqual(question.order, 10)
        self.assertFalse(self.test.is_published)

    def test_band_edit_and_publication_use_the_same_saved_version(self):
        self.make_valid()
        band = ResultBand.objects.create(assessment=self.test, minimum_percent=0, title="Результат", feedback="Старий текст")
        self.test.is_published = True
        self.test.save()
        data = self.assessment_data(_publish="1", **{"result_bands-TOTAL_FORMS": 1, "result_bands-INITIAL_FORMS": 1,
            "result_bands-0-id": band.pk, "result_bands-0-minimum_percent": 0,
            "result_bands-0-title": band.title, "result_bands-0-feedback": "Новий текст"})
        response = self.client.post(self.change_url(), data)
        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db()
        band.refresh_from_db()
        self.assertTrue(self.test.is_published)
        self.assertEqual(band.feedback, "Новий текст")

    def test_admin_with_view_only_permissions_can_read_without_edit_controls(self):
        question = self.make_valid()
        reviewer = User.objects.create_user(username="content-reviewer", role="admin", is_staff=True)
        reviewer.user_permissions.add(*Permission.objects.filter(content_type__app_label="knowledge", codename__startswith="view_"))
        self.client.force_login(reviewer)
        for url in (self.change_url(), reverse("admin:knowledge_question_change", args=[question.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'name="_publish"')
        self.assertEqual(self.client.post(self.change_url(), self.assessment_data(_publish="1")).status_code, 403)
