from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse

from courses.models import Course
from users.models import User
from .models import TrialRequest
from .trial_forms import TrialRequestForm


class TrialFormUsabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.beginner = Course.objects.create(title="Англійська для початківців", subject="english", price=0, is_published=True)
        cls.courses = list(Course.objects.filter(is_published=True))
        cls.math = next(course for course in cls.courses if course.subject == "math")
        cls.english = next(course for course in cls.courses if course.subject == "english" and course.grade_band)
        cls.student = User.objects.create_user(username="form-polish-user", first_name="Марія", email="polish@example.invalid")

    def data(self, **changes):
        return {"student_name": "Тестовий учень", "level": "7 клас", "age_category": "minor",
                "guardian_name": "Тестовий представник", "guardian_consent": "on", "privacy_consent": "on",
                "email": "trial-polish@example.invalid", "phone": "+380000000000", "requested_format": "individual", "comment": ""} | changes

    def form(self, **changes):
        return TrialRequestForm(self.data(**changes), course=self.math, user=AnonymousUser())

    def test_hints_match_all_seven_courses_and_subjects(self):
        self.assertEqual(len(self.courses), 7)
        for course in self.courses:
            response = self.client.get(reverse("trial_request", args=[course.pk]))
            hint = response.context["form"].fields["level"].help_text
            if course.subject == "math":
                self.assertEqual(hint, "Наприклад: 7 клас, потрібна допомога з алгеброю")
                self.assertNotIn("англійсь", hint)
            elif course.grade_band:
                self.assertEqual(hint, "Наприклад: 7 клас, рівень A1–A2")
            else:
                self.assertEqual(hint, "Наприклад: початковий рівень, раніше не вивчав(-ла) англійську")

    def test_authorized_prefill_is_preserved_for_all_courses(self):
        self.client.force_login(self.student)
        for course in self.courses:
            form = self.client.get(reverse("trial_request", args=[course.pk])).context["form"]
            self.assertEqual(form["student_name"].value(), self.student.first_name)
            self.assertEqual(form["email"].value(), self.student.email)

    def test_both_formats_are_accepted_for_adults_and_minors_on_each_course(self):
        for course in self.courses:
            for age in ("adult", "minor"):
                for fmt in ("individual", "group"):
                    with self.subTest(course=course.pk, age=age, format=fmt):
                        form = TrialRequestForm(self.data(age_category=age, requested_format=fmt), course=course, user=AnonymousUser())
                        self.assertTrue(form.is_valid(), form.errors)

    def test_minor_has_server_required_guardian_fields(self):
        form = self.form(guardian_name="", guardian_consent="")
        self.assertTrue(form.fields["guardian_name"].required)
        self.assertTrue(form.fields["guardian_consent"].required)
        self.assertFalse(form.is_valid())
        self.assertEqual(len(form.errors["guardian_name"]), 1)
        self.assertEqual(len(form.errors["guardian_consent"]), 1)

    def test_adult_discards_stale_or_malformed_guardian_fields(self):
        form = self.form(age_category="adult", guardian_name="x"*500, guardian_consent="on")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["guardian_name"], "")
        self.assertFalse(form.cleaned_data["guardian_consent"])
        self.assertTrue(form.guardian_fields_hidden)

    def test_adult_direct_post_saves_no_irrelevant_guardian_data(self):
        response = self.client.post(reverse("trial_request", args=[self.math.pk]), self.data(age_category="adult", guardian_name="Stale parent data"))
        self.assertEqual(response.status_code, 302)
        trial = TrialRequest.objects.get()
        self.assertFalse(trial.is_minor)
        self.assertEqual(trial.guardian_name, "")
        self.assertIsNone(trial.guardian_consent_at)

    def test_invalid_age_cannot_bypass_minor_validation(self):
        form = self.form(age_category="unknown", guardian_name="", guardian_consent="")
        self.assertFalse(form.is_valid())
        self.assertIn("age_category", form.errors)

    def test_privacy_consent_required_for_both_ages(self):
        for age in ("adult", "minor"):
            form = self.form(age_category=age, privacy_consent="")
            self.assertFalse(form.is_valid())
            self.assertIn("privacy_consent", form.errors)

    def test_adult_invalid_post_keeps_guardian_hidden_and_contact_errors(self):
        response = self.client.post(reverse("trial_request", args=[self.math.pk]), self.data(age_category="adult", email="bad"))
        self.assertTrue(response.context["form"].guardian_fields_hidden)
        self.assertIn("email", response.context["form"].errors)
        self.assertNotIn("guardian_name", response.context["form"].errors)
        self.assertFalse(TrialRequest.objects.exists())

    def test_minor_without_javascript_is_rejected_and_errors_remain_visible(self):
        response = self.client.post(reverse("trial_request", args=[self.math.pk]), self.data(guardian_name="", guardian_consent=""))
        form = response.context["form"]
        self.assertFalse(form.guardian_fields_hidden)
        self.assertIn("guardian_name", form.errors)
        self.assertIn("guardian_consent", form.errors)
        self.assertFalse(TrialRequest.objects.exists())

    def test_contacts_still_validated_for_adults(self):
        for invalid, field in (({"email": "bad"}, "email"), ({"phone": "bad"}, "phone")):
            form = self.form(age_category="adult", **invalid)
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)
