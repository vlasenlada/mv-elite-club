from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classrooms.models import Attendance, GroupMembership, StudyGroup
from enrollments.models import Enrollment, PackageAccount, PackageUsage
from scheduling.models import Lesson
from users.models import User
from .models import Course, CoursePackage


class PackageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.get(slug="english-primary")
        cls.student = User.objects.create_user(username="package_student")
        cls.teacher = User.objects.create_user(username="package_teacher", role="teacher")
        cls.admin = User.objects.create_superuser(username="package_admin", email="admin@example.invalid", password=None, role="admin")
        cls.package = CoursePackage.objects.get(course=cls.course, learning_format="individual", lesson_count=4)

    def apply(self, package=None, learning_format="individual"):
        self.client.force_login(self.student)
        data = {"requested_format": learning_format}
        if package:
            data["package_id"] = package.pk
        response = self.client.post(reverse("enroll_course", args=[self.course.pk]), data)
        self.assertEqual(response.status_code, 302)
        return Enrollment.objects.get(student=self.student)

    def account(self, learning_format="individual"):
        package = CoursePackage.objects.get(course=self.course, learning_format=learning_format, lesson_count=4)
        enrollment = self.apply(package, learning_format)
        enrollment.status = "approved"
        enrollment.save(update_fields=["status"])
        account = enrollment.package_account
        account.status = "confirmed"
        account.save()
        return account

    def past_lesson(self, enrollment, offset=0):
        start = timezone.now() - timedelta(days=5, hours=offset)
        return Lesson.objects.create(enrollment=enrollment, teacher=self.teacher,
            starts_at=start, ends_at=start + timedelta(hours=1), status="completed")

    def test_seed_has_six_unpriced_packages_for_each_seeded_course(self):
        for course in Course.objects.all():
            self.assertEqual(set(course.packages.values_list("learning_format", "lesson_count")),
                {(f, n) for f in ("individual", "group") for n in (4, 8, 12)})
            self.assertFalse(course.packages.exclude(price=None).exists())
            response = self.client.get(course.get_absolute_url())
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "12 занять")

    def test_per_lesson_tariffs_are_preserved_and_not_used_as_package_prices(self):
        self.course.individual_price = Decimal("999.00")
        self.course.save(update_fields=["individual_price"])
        self.assertIsNone(self.package.price)
        self.assertIsNone(self.package.unit_price)
        self.assertIsNone(self.course.learning_options[0]["packages"][0].saving_amount)

    def test_exact_package_unit_price_and_real_saving_against_nearest_priced_smaller(self):
        CoursePackage.objects.filter(course=self.course, learning_format="individual", lesson_count=4).update(price=Decimal("400.00"))
        CoursePackage.objects.filter(course=self.course, learning_format="individual", lesson_count=8).update(price=Decimal("720.00"))
        CoursePackage.objects.filter(course=self.course, learning_format="individual", lesson_count=12).update(price=Decimal("960.00"))
        packages = self.course.learning_options[0]["packages"]
        self.assertEqual([p.unit_price for p in packages], [Decimal("100.00"), Decimal("90.00"), Decimal("80.00")])
        self.assertEqual([p.saving_amount for p in packages], [None, Decimal("80.00"), Decimal("120.00")])
        self.assertEqual(packages[2].compared_to, 8)
        CoursePackage.objects.filter(course=self.course, learning_format="group", lesson_count=4).update(price="200.00")
        CoursePackage.objects.filter(course=self.course, learning_format="group", lesson_count=8).update(price="360.00")
        group_packages = self.course.learning_options[1]["packages"]
        self.assertEqual((group_packages[0].unit_price, group_packages[1].saving_amount), (Decimal("50.00"), Decimal("40.00")))
        self.assertEqual(self.course.learning_options[0]["packages"][0].unit_price, Decimal("100.00"))

    def test_no_discount_for_more_expensive_or_equal_unit_price(self):
        for count, price in [(4, "400.00"), (8, "800.00"), (12, "1500.00")]:
            CoursePackage.objects.filter(course=self.course, learning_format="individual", lesson_count=count).update(price=price)
        self.assertTrue(all(p.saving_amount is None for p in self.course.learning_options[0]["packages"]))

    def test_zero_and_missing_prices_are_distinct_and_negative_invalid(self):
        self.package.price = Decimal("0")
        self.assertEqual(self.package.unit_price, Decimal("0.00"))
        self.package.price = Decimal("-1")
        with self.assertRaises(ValidationError):
            self.package.full_clean()
        self.package.price = None
        self.package.lesson_count = 5
        with self.assertRaises(ValidationError):
            self.package.full_clean()

    def test_selected_package_is_snapshotted_without_payment_or_confirmed_balance(self):
        self.package.price = Decimal("400")
        self.package.save()
        enrollment = self.apply(self.package)
        other_student = User.objects.create_user(username="cannot_receive_selected_package")
        enrollment.student = other_student
        with self.assertRaises(ValidationError):
            enrollment.full_clean()
        enrollment.refresh_from_db()
        account = enrollment.package_account
        self.assertEqual((account.lesson_count, account.agreed_price, account.status), (4, Decimal("400"), "unconfirmed"))
        self.assertIsNone(account.remaining_count)
        enrollment.status = "approved"
        enrollment.save()
        account.refresh_from_db()
        self.assertIsNone(account.remaining_count)
        self.package.price = Decimal("800")
        self.package.save()
        account.refresh_from_db()
        self.assertEqual(account.agreed_price, Decimal("400"))
        self.assertContains(self.client.get(reverse("student_dashboard")), "Пакет: 4 заняття")

    def test_old_application_without_package_does_not_gain_an_account(self):
        enrollment = self.apply()
        self.assertIsNone(enrollment.selected_package_id)
        self.assertFalse(PackageAccount.objects.filter(enrollment=enrollment).exists())
        self.course.individual_price = Decimal("78.90")
        self.course.save(update_fields=["individual_price"])
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.current_lesson_price, Decimal("78.90"))
        self.assertContains(self.client.get(reverse("student_dashboard")), "Поточний тариф за одне заняття")

    def test_invalid_foreign_format_disabled_or_non_numeric_package_does_not_create_request(self):
        foreign = CoursePackage.objects.exclude(course=self.course).first()
        group = CoursePackage.objects.get(course=self.course, learning_format="group", lesson_count=4)
        self.package.is_active = False
        self.package.save()
        self.client.force_login(self.student)
        for package_id in [foreign.pk, group.pk, self.package.pk, "invalid", "9" * 200]:
            with self.subTest(package_id=str(package_id)[:12]):
                response = self.client.post(reverse("enroll_course", args=[self.course.pk]), {"requested_format": "individual", "package_id": package_id})
                self.assertEqual(response.status_code, 302)
                self.assertFalse(Enrollment.objects.filter(student=self.student).exists())

    def test_duplicate_request_does_not_change_selected_package_or_snapshot(self):
        enrollment = self.apply(self.package)
        other = CoursePackage.objects.get(course=self.course, learning_format="group", lesson_count=12)
        self.apply(other, "group")
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.selected_package_id, self.package.pk)
        self.assertEqual(PackageAccount.objects.count(), 1)

    def test_admin_edits_package_price_and_site_reflects_it(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:courses_coursepackage_change", args=[self.package.pk]), {
            "course": self.course.pk, "learning_format": "individual", "lesson_count": 4, "price": "456.78", "is_active": "on", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        self.package.refresh_from_db()
        self.assertEqual(self.package.price, Decimal("456.78"))
        self.assertEqual(self.package.unit_price, Decimal("114.20"))
        self.assertContains(self.client.get(self.course.get_absolute_url()), "456,78")

    def test_used_tariff_cannot_change_course_format_or_size(self):
        self.apply(self.package)
        self.package.lesson_count = 12
        with self.assertRaises(ValidationError):
            self.package.full_clean()

    def test_manual_confirmed_usage_counts_completed_lessons_only(self):
        account = self.account()
        self.assertEqual((account.used_count, account.remaining_count), (0, 4))
        lesson = self.past_lesson(account.enrollment)
        PackageUsage.objects.create(account=account, lesson=lesson, note="Фактичне заняття підтверджено")
        self.assertEqual((account.used_count, account.remaining_count), (1, 3))
        lesson.status = "cancelled"
        lesson.save(update_fields=["status"])
        self.assertEqual((account.used_count, account.remaining_count), (0, 4))
        self.assertEqual(account.usages.count(), 1)

    def test_unconfirmed_package_and_noncompleted_future_lessons_cannot_be_debited(self):
        enrollment = self.apply(self.package)
        enrollment.status = "approved"
        enrollment.save()
        lesson = self.past_lesson(enrollment)
        with self.assertRaises(ValidationError):
            PackageUsage.objects.create(account=enrollment.package_account, lesson=lesson, note="Не дозволено")
        account = enrollment.package_account
        account.status = "confirmed"
        account.save()
        for status in ("scheduled", "cancelled"):
            lesson.status = status
            lesson.save()
            with self.assertRaises(ValidationError):
                PackageUsage.objects.create(account=account, lesson=lesson, note="Не дозволено")

    def test_package_cannot_be_confirmed_before_enrollment_approval(self):
        account = self.apply(self.package).package_account
        account.status = "confirmed"
        with self.assertRaises(ValidationError):
            account.save()

    def test_unique_usage_and_limit_prevent_double_or_excess_debit(self):
        account = self.account()
        for offset in range(4):
            lesson = self.past_lesson(account.enrollment, offset)
            PackageUsage.objects.create(account=account, lesson=lesson, note="Підтверджено")
        self.assertEqual((account.used_count, account.remaining_count, account.display_status), (4, 0, "Вичерпано"))
        with self.assertRaises(ValidationError):
            PackageUsage.objects.create(account=account, lesson=lesson, note="Дубль")
        with self.assertRaises(ValidationError):
            PackageUsage.objects.create(account=account, lesson=self.past_lesson(account.enrollment, 10), note="Зайве")

    def test_student_cannot_edit_package_prices_or_confirm_balance_via_admin(self):
        enrollment = self.apply(self.package)
        for name, pk in [("admin:courses_coursepackage_change", self.package.pk), ("admin:enrollments_packageaccount_change", enrollment.package_account.pk)]:
            self.assertEqual(self.client.post(reverse(name, args=[pk]), {"status": "confirmed", "price": "1"}).status_code, 302)
        enrollment.package_account.refresh_from_db()
        self.assertEqual(enrollment.package_account.status, "unconfirmed")

    def test_group_shared_lesson_has_individual_ledger_and_absence_is_not_debited(self):
        account = self.account("group")
        group = StudyGroup.objects.create(course=self.course, teacher=self.teacher, name="Пакетна група", capacity=8)
        GroupMembership.objects.create(group=group, enrollment=account.enrollment, joined_at=timezone.now() - timedelta(days=10))
        start = timezone.now() - timedelta(days=2)
        lesson = Lesson.objects.create(group=group, teacher=self.teacher, starts_at=start, ends_at=start+timedelta(hours=1), status="completed")
        attendance = Attendance.objects.get(lesson=lesson)
        attendance.status = "absent"
        attendance.save()
        with self.assertRaises(ValidationError):
            PackageUsage.objects.create(account=account, lesson=lesson, note="Пропуск не списуємо")
        attendance.status = "present"
        attendance.save()
        PackageUsage.objects.create(account=account, lesson=lesson, note="Присутність і заняття підтверджено")
        self.assertEqual(account.remaining_count, 3)
        other = User.objects.create_user(username="independent_group_package")
        second = Enrollment.objects.create(student=other, course=self.course, requested_format="group",
            format_is_explicit=True, selected_package=account.enrollment.selected_package, status="approved")
        second_account = second.package_account
        second_account.status = "confirmed"
        second_account.save()
        self.assertEqual((second_account.used_count, second_account.remaining_count), (0, 4))

    def test_new_dashboard_navigation_and_real_empty_metrics(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("student_dashboard"))
        for label in ("Огляд", "Мої курси", "Розклад", "Домашні завдання", "Оплати", "Профіль"):
            self.assertContains(response, label)
        self.assertEqual((response.context["active_course_count"], response.context["upcoming_count"], response.context["pending_homework_count"]), (0, 0, 0))
        self.assertContains(response, "Обрати навчання")

    def test_guest_package_choice_survives_login_and_registration_destination(self):
        response = self.client.get(self.course.get_absolute_url())
        package = response.context["learning_options"][0]["packages"][0]
        self.assertIn(f"package={package.pk}", package.return_url)
        self.assertTrue(package.return_url.endswith(f"#package-{package.pk}"))
        response = self.client.get(reverse("login"), {"next": package.return_url})
        self.assertContains(response, "package%3D")
        response = self.client.get(self.course.get_absolute_url(), {"format": "individual", "package": self.package.pk})
        self.assertTrue(response.context["learning_options"][0]["packages"][0].selected)

    def test_restoring_cancelled_lesson_cannot_overdraw_confirmed_package(self):
        account = self.account()
        old = self.past_lesson(account.enrollment, 20)
        PackageUsage.objects.create(account=account, lesson=old, note="Підтверджено")
        old.status = "cancelled"
        old.save()
        for offset in range(4):
            PackageUsage.objects.create(account=account, lesson=self.past_lesson(account.enrollment, offset), note="Підтверджено")
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("update_lesson_status", args=[old.pk]), {"status": "completed"}, follow=True)
        self.assertEqual(response.status_code, 200)
        old.refresh_from_db()
        self.assertEqual(old.status, "cancelled")
        self.assertEqual(account.used_count, 4)

    def test_admin_can_explicitly_confirm_and_register_a_completed_lesson(self):
        enrollment = self.apply(self.package)
        enrollment.status = "approved"
        enrollment.save()
        account = enrollment.package_account
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:enrollments_packageaccount_change", args=[account.pk]),
            {"status": "confirmed", "agreed_price": "", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.remaining_count, 4)
        lesson = self.past_lesson(enrollment)
        response = self.client.post(reverse("admin:enrollments_packageusage_add"),
            {"account": account.pk, "lesson": lesson.pk, "note": "Фактичне заняття підтверджено", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(account.remaining_count, 3)

    def test_admin_can_attach_first_package_to_legacy_request_without_changing_history(self):
        enrollment = Enrollment.objects.create(student=self.student, course=self.course, status="approved", teacher=self.teacher)
        lesson = self.past_lesson(enrollment)
        before = list(Lesson.objects.values())
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:enrollments_enrollment_change", args=[enrollment.pk]), {
            "student": self.student.pk, "course": self.course.pk, "teacher": self.teacher.pk, "status": "approved",
            "requested_format": "individual", "selected_package": self.package.pk, "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        enrollment.refresh_from_db()
        self.assertFalse(enrollment.format_is_explicit)
        self.assertEqual(enrollment.package_account.status, "unconfirmed")
        self.assertEqual(list(Lesson.objects.values()), before)
