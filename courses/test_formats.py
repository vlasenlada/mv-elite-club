from decimal import Decimal
import re

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from users.models import User

from .models import Course


class CourseFormatTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title="Англійська для початківців", subject="english", description="Повний опис курсу",
            lesson_format="individual", price=Decimal("91.11"), is_published=True,
        )
        cls.admin = User.objects.create_superuser(username="price_admin", email="admin@example.com", password=None, role="admin")

    def admin_data(self, **overrides):
        return {
            "title": self.course.title, "subject": "english", "description": self.course.description,
            "card_tone": "cream", "supports_individual": "on", "supports_group": "on", "is_published": "on",
            "individual_price": "12.34", "group_price": "56.78", "_save": "Save", **overrides,
        }

    def format_card(self, response, name):
        match = re.search(r'<article[^>]*data-format="' + name + r'".*?</article>', response.content.decode(), re.S)
        self.assertIsNotNone(match)
        return match.group(0)

    def test_prices_are_independent_and_displayed_in_the_correct_card(self):
        self.course.individual_price = Decimal("12.34")
        self.course.group_price = Decimal("56.78")
        self.course.save(update_fields=["individual_price", "group_price"])
        response = self.client.get(self.course.get_absolute_url())
        individual = self.format_card(response, "individual")
        group = self.format_card(response, "group")
        self.assertIn("12,34", individual)
        self.assertNotIn("56,78", individual)
        self.assertIn("56,78", group)
        self.assertNotIn("12,34", group)
        self.assertIn("за одне заняття для одного учня", group)
        self.assertIn("Обрати індивідуальне навчання", individual)
        self.assertIn("Обрати групове навчання", group)

    def test_blank_tariffs_do_not_reuse_historical_price_or_display_zero(self):
        response = self.client.get(self.course.get_absolute_url())
        for name in ("individual", "group"):
            card = self.format_card(response, name)
            self.assertIn("Вартість уточнюється", card)
            self.assertNotIn("91,11", card)
            self.assertNotIn("0,00", card)
            self.assertNotIn("Безкоштовно", card)
        self.course.refresh_from_db()
        self.assertEqual(self.course.price, Decimal("91.11"))

    def test_explicit_zero_is_distinguished_from_missing_price(self):
        self.course.individual_price = Decimal("0")
        self.course.save(update_fields=["individual_price"])
        response = self.client.get(self.course.get_absolute_url())
        self.assertIn("0,00", self.format_card(response, "individual"))
        self.assertIn("Вартість уточнюється", self.format_card(response, "group"))

    def test_negative_tariffs_are_rejected(self):
        for name in ("individual_price", "group_price"):
            with self.subTest(field=name):
                setattr(self.course, name, Decimal("-0.01"))
                with self.assertRaises(ValidationError) as error:
                    self.course.full_clean()
                self.assertIn(name, error.exception.message_dict)
                setattr(self.course, name, None)

    def test_admin_edits_both_tariffs_without_changing_legacy_price(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:courses_course_change", args=[self.course.pk]), self.admin_data(price="999.99"))
        self.assertEqual(response.status_code, 302)
        self.course.refresh_from_db()
        self.assertEqual(self.course.individual_price, Decimal("12.34"))
        self.assertEqual(self.course.group_price, Decimal("56.78"))
        self.assertEqual(self.course.price, Decimal("91.11"))
        self.assertEqual(self.course.lesson_format, "individual")

    def test_admin_can_clear_a_tariff_and_site_reflects_it(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("admin:courses_course_change", args=[self.course.pk]), self.admin_data(group_price="")).status_code, 302)
        self.course.refresh_from_db()
        self.assertIsNone(self.course.group_price)
        response = self.client.get(self.course.get_absolute_url())
        self.assertIn("Вартість уточнюється", self.format_card(response, "group"))

    def test_admin_negative_price_is_form_error_without_500(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:courses_course_change", args=[self.course.pk]), self.admin_data(individual_price="-1"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("individual_price", response.context["adminform"].form.errors)
        self.course.refresh_from_db()
        self.assertIsNone(self.course.individual_price)

    def test_admin_can_create_course_without_filling_legacy_fields(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:courses_course_add"), self.admin_data(title="Новий напрям"))
        self.assertEqual(response.status_code, 302)
        course = Course.objects.get(title="Новий напрям")
        self.assertEqual(course.lesson_format, "individual")
        self.assertIsNone(course.price)

    def test_all_seven_courses_offer_two_formats_without_duplicates(self):
        self.assertEqual(Course.objects.count(), 7)
        for course in Course.objects.all():
            response = self.client.get(course.get_absolute_url())
            self.format_card(response, "individual")
            self.format_card(response, "group")
        self.assertEqual(Course.objects.filter(title=self.course.title).count(), 1)

    def test_unavailable_format_is_not_rendered(self):
        self.course.supports_group = False
        self.course.save(update_fields=["supports_group"])
        response = self.client.get(self.course.get_absolute_url())
        self.assertContains(response, 'data-format="individual"')
        self.assertNotContains(response, 'data-format="group"')

    def test_color_is_stable_on_home_catalog_and_filtered_catalog(self):
        self.course.card_tone = "blue"
        self.course.save(update_fields=["card_tone"])
        marker = f'data-course-id="{self.course.pk}" data-tone="blue"'
        for url in (reverse("home"), reverse("course_list"), reverse("course_list") + "?subject=english"):
            self.assertContains(self.client.get(url), marker)

    def test_unknown_card_color_cannot_replace_agreed_palette(self):
        self.course.card_tone = "burgundy"
        with self.assertRaises(ValidationError) as error:
            self.course.full_clean()
        self.assertIn("card_tone", error.exception.message_dict)
