import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from users.models import User
from .importing import import_bank, read_bank
from .models import Assessment, Attempt, CourseRecommendation, Option, Passage, Question, ResultBand
from .numbers import parse_number
from .services import grade_snapshot, snapshot_for


class KnowledgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.test = Assessment.objects.create(title="Математика 7 клас", slug="grade-seven", subject="math", grade=7, grade_band="middle", question_count=3, estimated_minutes=5, seo_title="Особливий SEO-заголовок", seo_description="Унікальний опис для пошуку")
        cls.single = Question.objects.create(assessment=cls.test, prompt="2 + 2?", topic="Додавання", explanation="SECRET_EXPLANATION_FOUR", order=1)
        cls.yes = Option.objects.create(question=cls.single, text="Чотири", is_correct=True)
        cls.no = Option.objects.create(question=cls.single, text="П’ять", order=2)
        cls.multi = Question.objects.create(assessment=cls.test, prompt="Парні числа", kind="multiple", topic="Подільність", explanation="Діляться на два", order=2)
        cls.two = Option.objects.create(question=cls.multi, text="2", is_correct=True)
        cls.four = Option.objects.create(question=cls.multi, text="4", is_correct=True, order=2)
        cls.three = Option.objects.create(question=cls.multi, text="3", order=3)
        cls.numeric = Question.objects.create(assessment=cls.test, prompt="Числова відповідь", kind="numeric", topic="Десяткові числа", explanation="SECRET_NUMERIC_EXPLANATION", numeric_answer="-2.5", tolerance="0.01", order=3)
        cls.course = Course.objects.get(slug="math-middle")
        CourseRecommendation.objects.create(assessment=cls.test, course=cls.course, minimum_percent=50)
        cls.test.is_published = True
        cls.test.save()
        cls.student = User.objects.create_user(username="knowledge-student")
        cls.teacher = User.objects.create_user(username="knowledge-teacher", role="teacher")
        cls.manager = User.objects.create_superuser(username="knowledge-manager", role="admin", email="knowledge@example.invalid", password="Knowledge-test-password-123")

    def start(self, client=None):
        client = client or self.client
        response = client.post(reverse("knowledge:start", args=["math", self.test.slug]))
        self.assertEqual(response.status_code, 302)
        return Attempt.objects.get(pk=response.url.split("/")[3])

    def url(self, name, attempt, number=None):
        args = [attempt.pk] + ([number] if number is not None else [])
        return reverse("knowledge:" + name, args=args)

    def finish(self, attempt):
        return self.client.post(self.url("summary", attempt))

    def answer(self, attempt, number, answer, move="next"):
        return self.client.post(self.url("question", attempt, number), {"answer": answer, "move": move})

    def test_public_catalog_subject_nmt_and_seo(self):
        for name, args in (("index", []), ("subject", ["english"]), ("subject", ["math"]), ("nmt", ["english"]), ("nmt", ["math"])):
            response = self.client.get(reverse("knowledge:"+name, args=args))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'name="description"')
            self.assertContains(response, 'rel="canonical"')
        response = self.client.get(self.test.get_absolute_url())
        self.assertContains(response, self.test.seo_title)
        self.assertContains(response, self.test.seo_description)
        self.assertContains(response, "не прогнозує бал НМТ")
        self.assertContains(self.client.get(reverse("home")), reverse("knowledge:index"))

    def test_grade_filter_and_new_specific_class_need_no_code(self):
        response = self.client.get(reverse("knowledge:subject", args=["math"]), {"grade": 7})
        self.assertContains(response, self.test.title)
        self.assertEqual(response["X-Robots-Tag"], "noindex, follow")
        self.assertNotContains(self.client.get(reverse("knowledge:subject", args=["math"]), {"grade": 6}), self.test.title)
        self.assertEqual(self.client.get(reverse("knowledge:subject", args=["math"]), {"grade": 99}).status_code, 400)
        self.assertEqual(self.client.get("/tests/science/").status_code, 404)

    def test_audience_filter_does_not_repeat_shared_labels(self):
        self.test.audience = "5–9 класи"
        self.test.save()
        second = Assessment.objects.create(title="Інший тест", slug="another", subject="math", question_count=1, audience="5–9 класи")
        Question.objects.create(assessment=second, prompt="Один?", kind="numeric", numeric_answer=1, explanation="Одиниця", topic="Лічба")
        second.is_published = True
        second.save()
        response = self.client.get(reverse("knowledge:index"))
        self.assertEqual(response.context["audiences"], ["5–9 класи"])

    def test_guest_start_requires_no_contact_or_user(self):
        attempt = self.start()
        self.assertIsNone(attempt.user_id)
        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(len(attempt.owner_digest), 64)
        response = self.client.get(self.url("question", attempt, 1))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="email"')

    def test_unknown_passage_is_a_validation_error(self):
        self.single.passage_id = 99999999
        with self.assertRaises(ValidationError):
            self.single.full_clean()

    def test_attempt_cannot_be_reassigned_to_another_user(self):
        attempt = self.start()
        attempt.user = self.teacher
        with self.assertRaises(ValidationError):
            attempt.save()

    def test_authenticated_user_can_resume_with_new_session(self):
        self.client.force_login(self.student)
        attempt = self.start()
        other = Client()
        other.force_login(self.student)
        self.assertEqual(other.get(self.url("question", attempt, 1)).status_code, 200)

    def test_foreign_guest_and_other_user_cannot_read_or_mutate(self):
        attempt = self.start()
        guest, signed_in = Client(), Client()
        signed_in.force_login(self.teacher)
        for foreign in (guest, signed_in):
            for name in ("summary", "result"):
                self.assertEqual(foreign.get(self.url(name, attempt)).status_code, 404)
            self.assertEqual(foreign.post(self.url("question", attempt, 1), {"answer": self.yes.pk}).status_code, 404)
        self.assertEqual(Client().get(self.url("question", attempt, 1)).status_code, 404)
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers, {})

    def test_no_correct_flags_explanations_numeric_answers_before_finish(self):
        attempt = self.start()
        for number in (1, 2, 3):
            response = self.client.get(self.url("question", attempt, number))
            self.assertNotContains(response, "SECRET_EXPLANATION_FOUR")
            self.assertNotContains(response, "SECRET_NUMERIC_EXPLANATION")
            self.assertNotContains(response, "-2.50000000")
            self.assertNotContains(response, "is_correct")
            self.assertNotIn("snapshot", response.context)
        self.assertEqual(self.client.get(self.url("result", attempt)).status_code, 302)

    def test_single_multiple_numeric_scoring_and_explanations(self):
        attempt = self.start()
        self.answer(attempt, 1, str(self.yes.pk))
        self.answer(attempt, 2, [str(self.two.pk), str(self.four.pk)])
        self.answer(attempt, 3, "−2,50", "summary")
        self.assertEqual(self.finish(attempt).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.result["correct"], 3)
        self.assertEqual(attempt.percentage, Decimal("100"))
        response = self.client.get(self.url("result", attempt))
        self.assertContains(response, "SECRET_EXPLANATION_FOUR")
        self.assertContains(response, reverse("trial_request", args=[self.course.pk]))
        self.assertIn("Додавання", attempt.result["strong"])

    def test_multiple_requires_exact_set_and_duplicates_do_not_add_points(self):
        snapshot = snapshot_for(self.test)
        for chosen in ([str(self.two.pk)], [str(self.two.pk), str(self.four.pk), str(self.three.pk)]):
            self.assertEqual(grade_snapshot(snapshot, {"2": chosen})["correct"], 0)
        self.assertEqual(grade_snapshot(snapshot, {"2": [str(self.two.pk), str(self.four.pk)]})["correct"], 1)

    def test_unknown_answer_ids_and_invalid_numbers_rejected(self):
        attempt = self.start()
        for number, value in ((1, "999999"), (2, [str(self.yes.pk)]), (3, "NaN"), (3, "1/2"), (3, "1,000.5"), (3, "1e3")):
            response = self.answer(attempt, number, value)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        attempt.refresh_from_db()
        self.assertFalse(attempt.answers)

    def test_numeric_tolerance_boundaries(self):
        snapshot = snapshot_for(self.test)
        for value, count in (("-2.49", 1), ("-2.51", 1), ("-2.489", 0)):
            self.assertEqual(grade_snapshot(snapshot, {"3": value})["correct"], count)
        for invalid in ("NaN", "Infinity", "1e10", "1 000", "1/2", "1.2.3", "1,2.3", "9"*17, "0.123456789"):
            with self.assertRaises(ValidationError): parse_number(invalid)
        self.assertEqual(parse_number(" -0,25 "), Decimal("-.25"))

    def test_navigation_refresh_and_zero_value_preserved(self):
        attempt = self.start()
        self.assertEqual(self.answer(attempt, 3, "0", "previous").url, self.url("question", attempt, 2))
        response = self.client.get(self.url("question", attempt, 3))
        self.assertEqual(response.context["form"]["answer"].value(), "0")
        self.assertEqual(response.context["answered"], 1)

    def test_skipped_answers_wrong_and_topics_show_repeat(self):
        attempt = self.start()
        self.finish(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.result["correct"], 0)
        self.assertEqual(len(attempt.result["repeat"]), 3)
        self.assertFalse(attempt.result["recommendations"])

    def test_finish_idempotent_and_completed_attempt_read_only(self):
        attempt = self.start()
        self.answer(attempt, 1, str(self.yes.pk))
        self.finish(attempt)
        attempt.refresh_from_db()
        result, finished = attempt.result, attempt.completed_at
        self.finish(attempt)
        self.answer(attempt, 1, str(self.no.pk))
        attempt.refresh_from_db()
        self.assertEqual((attempt.result, attempt.completed_at), (result, finished))
        attempt.result = {}
        with self.assertRaises(ValidationError): attempt.save()

    def test_editing_published_question_hides_test_but_preserves_running_snapshot(self):
        attempt = self.start()
        self.yes.is_correct = False
        self.yes.save()
        self.no.is_correct = True
        self.no.save()
        self.single.explanation = "CHANGED_EXPLANATION"
        self.single.save()
        self.test.refresh_from_db()
        self.assertFalse(self.test.is_published)
        self.answer(attempt, 1, str(self.yes.pk))
        self.finish(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.result["correct"], 1)
        self.assertEqual(attempt.result["details"][0]["explanation"], "SECRET_EXPLANATION_FOUR")

    def test_finished_history_unchanged_after_deletion_of_questions(self):
        attempt = self.start()
        self.finish(attempt)
        attempt.refresh_from_db()
        result = attempt.result
        self.single.delete()
        self.numeric.delete()
        self.assertEqual(self.client.get(self.url("result", attempt)).status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.result, result)

    def test_noop_content_save_does_not_hide_published_test(self):
        self.yes.save()
        self.test.refresh_from_db()
        self.assertTrue(self.test.is_published)

    def test_reading_passage_and_explanation_are_frozen(self):
        passage = Passage.objects.create(assessment=self.test, title="Original passage", text="Original reading text")
        self.single.passage = passage
        self.single.save()
        self.test.is_published = True
        self.test.save()
        attempt = self.start()
        passage.text = "Changed passage"
        passage.save()
        self.assertContains(self.client.get(self.url("question", attempt, 1)), "Original reading text")

    def test_attempt_snapshot_cannot_be_rewritten(self):
        attempt = self.start()
        attempt.snapshot["title"] = "Changed"
        with self.assertRaises(ValidationError): attempt.save()

    def test_expired_active_attempt_and_historical_result(self):
        attempt = self.start()
        Attempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get(self.url("question", attempt, 1)).status_code, 410)
        self.assertEqual(self.finish(attempt).status_code, 410)

    def test_private_headers_and_sitemap_exclude_attempts_and_drafts(self):
        attempt = self.start()
        for name, number in (("question", 1), ("summary", None)):
            response = self.client.get(self.url(name, attempt, number))
            self.assertIn("noindex", response["X-Robots-Tag"])
            self.assertIn("no-store", response["Cache-Control"])
        sitemap = self.client.get(reverse("knowledge:sitemap"))
        self.assertContains(sitemap, self.test.get_absolute_url())
        self.assertNotContains(sitemap, str(attempt.pk))
        self.test.is_published = False
        self.test.save()
        self.assertNotContains(self.client.get(reverse("knowledge:sitemap")), self.test.get_absolute_url())
        self.assertEqual(self.client.get(self.test.get_absolute_url()).status_code, 404)

    def test_csrf_required_for_start_answers_finish(self):
        client = Client(enforce_csrf_checks=True)
        start = reverse("knowledge:start", args=["math", self.test.slug])
        self.assertEqual(client.post(start).status_code, 403)
        client.get(self.test.get_absolute_url())
        token = client.cookies["csrftoken"].value
        response = client.post(start, {"csrfmiddlewaretoken": token})
        attempt = Attempt.objects.get(pk=response.url.split("/")[3])
        self.assertEqual(client.post(self.url("question", attempt, 1), {"answer": self.yes.pk}).status_code, 403)
        self.assertEqual(client.post(self.url("summary", attempt)).status_code, 403)

    def test_rate_limit_start(self):
        url = reverse("knowledge:start", args=["math", self.test.slug])
        for _ in range(10): self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 429)
        self.assertEqual(Attempt.objects.count(), 10)

    def test_recommendations_validate_context_and_live_publication(self):
        with self.assertRaises(ValidationError):
            CourseRecommendation.objects.create(assessment=self.test, course=Course.objects.get(slug="english-middle"))
        with self.assertRaises(ValidationError):
            CourseRecommendation.objects.create(assessment=self.test, course=Course.objects.get(slug="math-primary"))
        attempt = self.start()
        self.answer(attempt, 1, str(self.yes.pk))
        self.answer(attempt, 3, "-2.5", "summary")
        self.finish(attempt)
        self.course.is_published = False
        self.course.save()
        self.assertNotContains(self.client.get(self.url("result", attempt)), reverse("trial_request", args=[self.course.pk]))

    def test_publication_checks_pending_grade_changes_against_course_rules(self):
        self.test.grade = 3
        self.test.grade_band = "primary"
        with self.assertRaises(ValidationError): self.test.save()

    def test_cannot_publish_insufficient_invalid_or_missing_explanation(self):
        draft = Assessment.objects.create(title="Draft", slug="draft", subject="math", question_count=1)
        draft.is_published = True
        with self.assertRaises(ValidationError): draft.save()
        question = Question.objects.create(assessment=draft, prompt="Question", topic="Topic")
        Option.objects.create(question=question, text="Yes", is_correct=True)
        Option.objects.create(question=question, text="No", order=2)
        with self.assertRaises(ValidationError): draft.save()
        question.explanation = "Explanation"
        question.save()
        draft.save()
        self.assertTrue(draft.is_published)

    def test_publication_rejects_wrong_single_multiple_numeric_structure(self):
        for question, change in ((self.single, {"kind": "multiple"}), (self.multi, {"kind": "single"}), (self.numeric, {"numeric_answer": None})):
            for key, value in change.items(): setattr(question, key, value)
            question.save()
            with self.assertRaises(ValidationError):
                self.test.is_published = True
                self.test.save()

    def test_passage_cannot_cross_tests(self):
        other = Assessment.objects.create(title="Other", slug="other", subject="english")
        passage = Passage.objects.create(assessment=other, text="Other text")
        self.single.passage = passage
        with self.assertRaises(ValidationError): self.single.save()

    def test_level_estimation_requires_evidence_and_never_appears_unreviewed(self):
        test = Assessment.objects.create(title="English", slug="english-level", subject="english", question_count=1)
        question = Question.objects.create(assessment=test, prompt="Choose", topic="Topic", explanation="Explain")
        Option.objects.create(question=question, text="One", is_correct=True)
        Option.objects.create(question=question, text="Two", order=2)
        ResultBand.objects.create(assessment=test, minimum_percent=0, title="Basic skills", feedback="Review the topic", level_hint="A1")
        snapshot = snapshot_for(test)
        self.assertEqual(snapshot["bands"][0]["level_hint"], "")
        test.level_estimation_enabled = True
        test.is_published = True
        with self.assertRaises(ValidationError): test.save()
        test.methodology_notes = "Human-reviewed method fixture"
        test.save()
        self.assertEqual(snapshot_for(test)["bands"][0]["level_hint"], "A1")

    def test_admin_add_forms_options_and_aggregate_stats(self):
        self.client.force_login(self.manager)
        for model in ("assessment", "question", "passage", "resultband", "courserecommendation"):
            self.assertEqual(self.client.get(reverse("admin:knowledge_"+model+"_changelist")).status_code, 200)
            self.assertEqual(self.client.get(reverse("admin:knowledge_"+model+"_add")).status_code, 200)
        response = self.client.get(reverse("admin:knowledge_question_change", args=[self.single.pk]))
        self.assertContains(response, "Варіанти відповідей")
        self.assertContains(response, self.yes.text)
        self.assertNotIn(Attempt, admin.site._registry)

    def test_teacher_and_student_denied_even_with_model_permissions(self):
        for user in (self.student, self.teacher):
            user.is_staff = True
            user.save()
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="knowledge"))
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("admin:knowledge_question_change", args=[self.single.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse("admin:knowledge_assessment_changelist"), {"action": "publish_selected", "_selected_action": self.test.pk}).status_code, 403)

    def test_admin_can_add_test_question_and_answers_without_code(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("admin:knowledge_assessment_add"), {
            "title": "Admin test", "slug": "admin-test", "subject": "math", "category": "general", "grade_band": "middle", "grade": 7,
            "question_count": 1, "estimated_minutes": 5, "result_bands-TOTAL_FORMS": 0, "result_bands-INITIAL_FORMS": 0,
            "recommendations-TOTAL_FORMS": 0, "recommendations-INITIAL_FORMS": 0, "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        test = Assessment.objects.get(slug="admin-test")
        response = self.client.post(reverse("admin:knowledge_question_add"), {
            "assessment": test.pk, "order": 1, "is_active": "on", "topic": "Addition", "difficulty": "easy", "kind": "single", "prompt": "One plus one?",
            "explanation": "Two units make two", "tolerance": 0, "options-TOTAL_FORMS": 2, "options-INITIAL_FORMS": 0,
            "options-0-text": "2", "options-0-is_correct": "on", "options-0-order": 1,
            "options-1-text": "3", "options-1-order": 2, "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        self.client.post(reverse("admin:knowledge_assessment_changelist"), {"action": "publish_selected", "_selected_action": test.pk})
        test.refresh_from_db()
        self.assertTrue(test.is_published)

    def test_xss_escaped_in_questions(self):
        self.single.prompt = '<script>alert("x")</script>'
        self.single.save()
        self.test.is_published = True
        self.test.save()
        attempt = self.start()
        self.assertNotContains(self.client.get(self.url("question", attempt, 1)), self.single.prompt)

    def test_missing_migration_guard_keeps_existing_pages_working(self):
        with patch("knowledge.views.storage_ready", return_value=False):
            self.assertEqual(self.client.get(reverse("knowledge:index")).status_code, 503)
            self.assertEqual(self.client.get(reverse("home")).status_code, 200)


class KnowledgeImportTests(TestCase):
    def test_author_bank_dry_run_apply_idempotency_and_no_publication(self):
        out = StringIO()
        call_command("seed_knowledge_examples", stdout=out)
        self.assertEqual(Assessment.objects.count(), 0)
        before = list(Course.objects.values())
        call_command("seed_knowledge_examples", apply=True, stdout=out)
        self.assertEqual(Assessment.objects.count(), 7)
        self.assertEqual(Question.objects.count(), 15)
        self.assertFalse(Assessment.objects.filter(is_published=True).exists())
        call_command("seed_knowledge_examples", apply=True, stdout=out)
        self.assertEqual(Question.objects.count(), 15)
        self.assertEqual(list(Course.objects.values()), before)
        for test in Assessment.objects.all(): test.validate_content()

    def test_safe_import_rejects_unknown_fields_publication_and_wrong_correct_flag(self):
        source = Path(__file__).parent/"data"/"examples.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        for mutate in (lambda b: b["tests"][0].update(is_published=True), lambda b: b["tests"][0]["questions"][0]["options"][0].update(is_correct="false")):
            value = json.loads(json.dumps(data))
            mutate(value)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/"bank.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValidationError): read_bank(path)
        self.assertEqual(Assessment.objects.count(), 0)

    def test_import_is_atomic_and_never_overwrites(self):
        bank = read_bank(Path(__file__).parent/"data"/"examples.json")
        import_bank(bank)
        with self.assertRaises(ValidationError): import_bank(bank)
        self.assertEqual(Assessment.objects.count(), 7)
        self.assertEqual(Question.objects.count(), 15)

    def test_import_command_defaults_to_no_writes(self):
        path = Path(__file__).parent/"data"/"examples.json"
        call_command("import_knowledge_bank", str(path), stdout=StringIO())
        self.assertEqual(Assessment.objects.count(), 0)

    def test_late_invalid_course_rolls_back_entire_import(self):
        bank = read_bank(Path(__file__).parent/"data"/"examples.json")
        bank["tests"][1]["recommendations"] = [{"course_id": 999999}]
        with self.assertRaises(ValidationError): import_bank(bank)
        self.assertEqual(Assessment.objects.count(), 0)
        self.assertEqual(Question.objects.count(), 0)
