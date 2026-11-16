import importlib
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from enrollments.models import Enrollment
from users.models import User

from .models import Course


class CatalogTests(TestCase):
    def test_six_directions_have_unique_pages_and_no_invented_prices(self):
        courses = Course.objects.exclude(slug=None)
        self.assertEqual(courses.count(), 6)
        for course in courses:
            with self.subTest(course=course.slug):
                self.assertIsNone(course.price)
                self.assertTrue(course.supports_individual and course.supports_group)
                self.assertTrue(course.program_items)
                self.assertTrue(course.benefit_items)
                response = self.client.get(course.get_absolute_url())
                self.assertContains(response, course.title)
                self.assertContains(response, "ТАРИФ")
                self.assertContains(response, "Вартість уточнюється")
                self.assertEqual(self.client.get(reverse("course_detail", args=[course.pk])).status_code, 200)

    def test_home_and_catalog_show_all_directions(self):
        for url in (reverse("home"), reverse("course_list")):
            response = self.client.get(url)
            for course in Course.objects.filter(is_published=True):
                self.assertContains(response, course.title)

    def test_catalog_filters_by_subject_and_age(self):
        response = self.client.get(reverse("course_list"), {"subject": "math", "grade": "nmt"})
        self.assertContains(response, "Підготовка до НМТ з математики")
        self.assertNotContains(response, "Підготовка до НМТ з англійської мови")

    def test_unpublished_course_is_not_public(self):
        course = Course.objects.get(slug="english-primary")
        course.is_published = False
        course.save(update_fields=["is_published"])
        self.assertEqual(self.client.get(course.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse("course_list")), course.title)

    def test_nmt_filter_includes_both_subjects(self):
        response = self.client.get(reverse("course_list"), {"subject": "nmt"})
        self.assertContains(response, "Підготовка до НМТ з математики")
        self.assertContains(response, "Підготовка до НМТ з англійської мови")
        self.assertNotContains(response, "Англійська мова — 1–4 класи")

    def test_seed_is_idempotent_and_does_not_overwrite_admin_edits(self):
        course = Course.objects.get(slug="english-primary")
        course.description = "Administrator content"
        course.save(update_fields=["description"])
        migration = importlib.import_module("courses.migrations.0003_seed_school_catalog")
        migration.seed_catalog(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Course.objects.count(), 6)
        course.refresh_from_db()
        self.assertEqual(course.description, "Administrator content")

    def test_enrollment_records_selected_group_format_without_duplicate(self):
        student = User.objects.create_user(username="catalog_student")
        course = Course.objects.get(slug="english-primary")
        self.client.force_login(student)
        url = reverse("enroll_course", args=[course.pk])
        self.assertEqual(self.client.post(url, {"requested_format": "group"}).status_code, 302)
        enrollment = Enrollment.objects.get(student=student, course=course)
        self.assertEqual(enrollment.requested_format, "group")
        self.client.post(url, {"requested_format": "individual"})
        self.assertEqual(Enrollment.objects.filter(student=student, course=course).count(), 1)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.requested_format, "group")

    def test_unsupported_or_invalid_format_does_not_create_enrollment(self):
        student = User.objects.create_user(username="catalog_student")
        course = Course.objects.get(slug="english-primary")
        course.supports_group = False
        course.save(update_fields=["supports_group"])
        self.client.force_login(student)
        for requested_format in ("group", "invalid"):
            response = self.client.post(reverse("enroll_course", args=[course.pk]), {"requested_format": requested_format})
            self.assertEqual(response.status_code, 302)
        self.assertFalse(Enrollment.objects.filter(student=student).exists())
