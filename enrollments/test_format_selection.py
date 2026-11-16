from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from classrooms.models import GroupMembership, StudyGroup
from courses.models import Course
from scheduling.forms import TeacherLessonForm
from scheduling.models import Lesson
from users.models import User

from .models import Enrollment


class FormatSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.get(slug="english-primary")
        cls.student = User.objects.create_user(username="format_student")
        cls.teacher = User.objects.create_user(username="format_teacher", role="teacher")
        cls.admin = User.objects.create_superuser(username="format_admin", email="admin@example.com", password=None, role="admin")

    def apply(self, lesson_format):
        self.client.force_login(self.student)
        response = self.client.post(reverse("enroll_course", args=[self.course.pk]), {"requested_format": lesson_format})
        self.assertEqual(response.status_code, 302)
        return Enrollment.objects.get(student=self.student, course=self.course)

    def approve(self, enrollment):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("update_enrollment_status", args=[enrollment.pk]), {"status": "approved"}).status_code, 302)
        enrollment.refresh_from_db()

    def lesson(self, enrollment):
        start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        return Lesson(enrollment=enrollment, teacher=self.teacher, starts_at=start, ends_at=start + timedelta(hours=1))

    def test_individual_button_posts_explicit_individual_format(self):
        self.client.force_login(self.student)
        response = self.client.get(self.course.get_absolute_url())
        self.assertContains(response, 'name="requested_format" value="individual"')
        self.assertContains(response, 'name="requested_format" value="group"')
        enrollment = self.apply("individual")
        self.assertTrue(enrollment.format_is_explicit)
        self.assertEqual(enrollment.requested_format, "individual")
        self.assertEqual(enrollment.status, "pending")

    def test_group_application_waits_without_auto_creating_a_group(self):
        enrollment = self.apply("group")
        self.assertTrue(enrollment.format_is_explicit)
        self.assertEqual(enrollment.requested_format, "group")
        self.assertIsNone(enrollment.teacher_id)
        self.assertEqual(StudyGroup.objects.count(), 0)
        self.assertEqual(GroupMembership.objects.count(), 0)

    def test_repeat_application_and_other_format_do_not_duplicate_or_overwrite(self):
        enrollment = self.apply("group")
        first_pk = enrollment.pk
        self.apply("group")
        self.apply("individual")
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.pk, first_pk)
        self.assertEqual(enrollment.requested_format, "group")
        self.assertEqual(Enrollment.objects.filter(student=self.student, course=self.course).count(), 1)

    def test_format_is_visible_to_student_and_administrator(self):
        enrollment = self.apply("group")
        self.assertContains(self.client.get(reverse("student_dashboard")), "Групові заняття")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("school_admin_dashboard"))
        self.assertContains(response, "Групові заняття")
        self.assertContains(response, "Групове навчання")
        response = self.client.get(reverse("admin:enrollments_enrollment_change", args=[enrollment.pk]))
        self.assertContains(response, 'value="group" selected')

    def test_individual_teacher_can_be_assigned_after_approval(self):
        enrollment = self.apply("individual")
        self.approve(enrollment)
        self.client.post(reverse("assign_enrollment_teacher", args=[enrollment.pk]), {"teacher_id": self.teacher.pk})
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.teacher_id, self.teacher.pk)
        self.lesson(enrollment).full_clean()

    def test_group_approval_allows_manual_group_admission(self):
        enrollment = self.apply("group")
        self.approve(enrollment)
        group = StudyGroup.objects.create(name="Нова група", course=self.course, teacher=self.teacher, capacity=3)
        GroupMembership.objects.create(group=group, enrollment=enrollment)
        self.assertEqual(group.member_count, 1)
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.teacher_id)

    def test_new_individual_request_cannot_be_admitted_to_group(self):
        enrollment = self.apply("individual")
        self.approve(enrollment)
        group = StudyGroup.objects.create(name="Група", course=self.course, teacher=self.teacher, capacity=3)
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(group=group, enrollment=enrollment)
        self.assertEqual(group.member_count, 0)

    def test_new_group_request_cannot_be_used_for_individual_lesson(self):
        enrollment = self.apply("group")
        self.approve(enrollment)
        with self.assertRaises(ValidationError):
            self.lesson(enrollment).full_clean()
        form = TeacherLessonForm(teacher=self.teacher)
        self.assertFalse(form.fields["enrollment"].queryset.filter(pk=enrollment.pk).exists())

    def test_new_group_request_does_not_receive_individual_teacher(self):
        enrollment = self.apply("group")
        self.approve(enrollment)
        response = self.client.post(reverse("assign_enrollment_teacher", args=[enrollment.pk]), {"teacher_id": self.teacher.pk}, follow=True)
        self.assertContains(response, "призначається навчальній групі")
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.teacher_id)

    def test_pending_group_application_cannot_be_admitted(self):
        enrollment = self.apply("group")
        group = StudyGroup.objects.create(name="Група", course=self.course, teacher=self.teacher, capacity=3)
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(group=group, enrollment=enrollment)

    def test_legacy_individual_application_keeps_existing_group_compatibility(self):
        enrollment = Enrollment.objects.create(student=self.student, course=self.course, status="approved", requested_format="individual")
        self.assertFalse(enrollment.format_is_explicit)
        group = StudyGroup.objects.create(name="Історична сумісність", course=self.course, teacher=self.teacher, capacity=3)
        GroupMembership.objects.create(group=group, enrollment=enrollment)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.requested_format, "individual")
        self.assertFalse(enrollment.format_is_explicit)

    def test_format_of_historical_application_with_lessons_cannot_change(self):
        enrollment = self.apply("individual")
        self.approve(enrollment)
        lesson = self.lesson(enrollment)
        lesson.save()
        enrollment.requested_format = "group"
        with self.assertRaises(ValidationError):
            enrollment.full_clean()
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.requested_format, "individual")
        self.assertEqual(Lesson.objects.get(pk=lesson.pk).enrollment_id, enrollment.pk)

    def test_unavailable_format_is_rejected_on_direct_post(self):
        self.course.supports_group = False
        self.course.save(update_fields=["supports_group"])
        self.client.force_login(self.student)
        self.client.post(reverse("enroll_course", args=[self.course.pk]), {"requested_format": "group"})
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_teacher_cannot_submit_student_application(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("enroll_course", args=[self.course.pk]), {"requested_format": "group"})
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_guest_post_requires_login(self):
        response = self.client.post(reverse("enroll_course", args=[self.course.pk]), {"requested_format": "group"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_new_admin_application_has_explicit_format(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:enrollments_enrollment_add"), {
            "student": self.student.pk, "course": self.course.pk, "requested_format": "group", "teacher": "", "status": "pending", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.get(student=self.student)
        self.assertTrue(enrollment.format_is_explicit)
        self.assertEqual(enrollment.requested_format, "group")


class RegistrationFormatReturnTests(TestCase):
    password = "TestOnly-K7v4!x23"

    def registration_data(self, **overrides):
        return {"username": "new_format_student", "email": "student@example.com", "password1": self.password,
                "password2": self.password, **overrides}

    def test_registration_preserves_selected_format_and_student_role(self):
        course = Course.objects.get(slug="english-primary")
        target = course.get_absolute_url() + "?format=group#learning-formats"
        response = self.client.post(reverse("register"), self.registration_data(next=target, role="admin", is_staff="on", is_superuser="on"))
        self.assertRedirects(response, target)
        student = User.objects.get(username="new_format_student")
        self.assertEqual(student.role, "student")
        self.assertFalse(student.is_staff or student.is_superuser)
        response = self.client.get(course.get_absolute_url(), {"format": "group"})
        self.assertTrue(next(option for option in response.context["learning_options"] if option["value"] == "group")["selected"])

    def test_registration_rejects_external_return_url(self):
        response = self.client.post(reverse("register"), self.registration_data(next="https://example.invalid/external"))
        self.assertRedirects(response, reverse("student_dashboard"))

    def test_login_and_logout_preserve_normal_authentication_flow(self):
        user = User.objects.create_user(username="login_student", password=self.password)
        target = "/courses/english-primary/?format=individual#learning-formats"
        response = self.client.post(reverse("login"), {"username": user.username, "password": self.password, "next": target})
        self.assertRedirects(response, target)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("home"))

    def test_guest_format_buttons_preserve_choice_in_login_destination(self):
        course = Course.objects.get(slug="english-primary")
        response = self.client.get(course.get_absolute_url())
        self.assertContains(response, "format%3Dgroup%23learning-formats")
        self.assertContains(response, "format%3Dindividual%23learning-formats")
