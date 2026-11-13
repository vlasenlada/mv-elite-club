from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from learning.models import Assignment, Submission
from scheduling.models import Lesson
from users.models import User
from users.teacher_assignment import TEACHER_ERROR

from .models import Enrollment
from .services import transfer_enrollment_teacher


class TeacherAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(username="teacher", role="teacher")
        cls.new_teacher = User.objects.create_user(username="new_teacher", role="teacher")
        cls.substitute = User.objects.create_user(username="substitute", role="teacher")
        cls.student = User.objects.create_user(username="student")
        cls.other_student = User.objects.create_user(username="other_student")
        cls.school_admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password=None, role="admin"
        )
        cls.inactive_teacher = User.objects.create_user(
            username="inactive", role="teacher", is_active=False
        )
        cls.staff_teacher = User.objects.create_user(
            username="staff_teacher", role="teacher", is_staff=True
        )
        cls.course, cls.other_course = [Course.objects.create(
            title=title, subject="english", description="Course",
            lesson_format="individual", price="100.00",
        ) for title in ("English", "Advanced English")]
        cls.enrollment = Enrollment.objects.create(
            student=cls.student, teacher=cls.teacher, course=cls.course, status="approved"
        )
        cls.other_enrollment = Enrollment.objects.create(
            student=cls.student, teacher=cls.teacher, course=cls.other_course, status="approved"
        )
        cls.start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)

        def lesson(start, *, status="scheduled", teacher=None, enrollment=None):
            return Lesson.objects.create(
                enrollment=enrollment or cls.enrollment,
                teacher=teacher or cls.teacher,
                starts_at=start, ends_at=start + timedelta(hours=1), status=status,
                meeting_url="https://example.com/unchanged-meeting",
            )

        cls.future = lesson(cls.start)
        cls.second_future = lesson(cls.start + timedelta(hours=2))
        cls.substituted_future = lesson(cls.start + timedelta(hours=3), teacher=cls.substitute)
        cls.completed_future = lesson(cls.start + timedelta(hours=4), status="completed")
        cls.cancelled_future = lesson(cls.start + timedelta(hours=5), status="cancelled")
        cls.other_course_lesson = lesson(
            cls.start + timedelta(hours=6), enrollment=cls.other_enrollment
        )
        cls.past = lesson(cls.start - timedelta(days=5), status="completed")
        cls.past_scheduled = lesson(cls.start - timedelta(days=4))
        cls.in_progress = lesson(timezone.now() - timedelta(minutes=20))
        cls.homework = Assignment.objects.create(
            lesson=cls.past, title="Historical homework", description="Historical task"
        )
        cls.answer = Submission.objects.create(
            assignment=cls.homework, student=cls.student, answer_text="Historical answer",
            grade=91, feedback="Historical feedback",
        )

    def setUp(self):
        self.client.force_login(self.school_admin)
        self.course_url = reverse("assign_enrollment_teacher", args=[self.enrollment.pk])
        self.lesson_url = reverse("assign_lesson_teacher", args=[self.future.pk])

    def snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (Enrollment, Lesson, Assignment, Submission)
        }

    def assert_rejected(self, url, data, message=None):
        before = self.snapshot()
        response = self.client.post(url, data, follow=True)
        self.assertRedirects(response, reverse("school_admin_dashboard"))
        if message:
            self.assertContains(response, message)
        self.assertEqual(self.snapshot(), before)
        return response

    def add_future_homework(self):
        homework = Assignment.objects.create(
            lesson=self.future, title="Future homework", description="Task"
        )
        Submission.objects.create(
            assignment=homework, student=self.student, answer_text="Future answer", grade=75
        )

    def add_target_conflict(self, start):
        enrollment = Enrollment.objects.create(
            student=self.other_student, course=self.course,
            teacher=self.new_teacher, status="approved",
        )
        return Lesson.objects.create(
            enrollment=enrollment, teacher=self.new_teacher,
            starts_at=start, ends_at=start + timedelta(hours=1),
        )

    def admin_enrollment_data(self, teacher_id, **kwargs):
        return {
            "student": self.student.pk, "course": self.course.pk,
            "teacher": teacher_id, "status": "approved", "_save": "Save", **kwargs,
        }

    def admin_lesson_data(self, lesson, teacher_id):
        return {
            "enrollment": lesson.enrollment_id, "teacher": teacher_id,
            "starts_at_0": timezone.localtime(lesson.starts_at).strftime("%Y-%m-%d"),
            "starts_at_1": timezone.localtime(lesson.starts_at).strftime("%H:%M:%S"),
            "ends_at_0": timezone.localtime(lesson.ends_at).strftime("%Y-%m-%d"),
            "ends_at_1": timezone.localtime(lesson.ends_at).strftime("%H:%M:%S"),
            "status": lesson.status, "meeting_url": lesson.meeting_url, "_save": "Save",
        }

    def test_empty_malformed_and_missing_teacher_id_do_not_crash(self):
        for url in (self.course_url, self.lesson_url):
            for data in ({}, {"teacher_id": ""}, {"teacher_id": " "},
                         {"teacher_id": "abc"}, {"teacher_id": "1.5"},
                         {"teacher_id": "-1"}, {"teacher_id": "9" * 40}):
                with self.subTest(url=url, data=data):
                    response = self.assert_rejected(url, data)
                    self.assertTrue(list(response.wsgi_request._messages))

    def test_student_admin_inactive_and_staff_cannot_be_assigned(self):
        for url in (self.course_url, self.lesson_url):
            for user in (self.student, self.school_admin, self.inactive_teacher, self.staff_teacher):
                with self.subTest(url=url, user=user.username):
                    self.assert_rejected(url, {"teacher_id": user.pk}, TEACHER_ERROR)

    def test_initial_assignment_is_scoped_to_the_selected_course(self):
        self.enrollment.teacher = None
        self.enrollment.save(update_fields=["teacher"])
        response = self.client.post(self.course_url, {"teacher_id": self.new_teacher.pk})
        self.assertRedirects(response, reverse("school_admin_dashboard"))
        self.enrollment.refresh_from_db()
        self.other_enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.teacher_id, self.new_teacher.pk)
        self.assertEqual(self.other_enrollment.teacher_id, self.teacher.pk)

    def test_transfer_changes_only_future_scheduled_lessons_of_selected_course(self):
        before = self.snapshot()
        response = self.client.post(self.course_url, {"teacher_id": self.new_teacher.pk}, follow=True)
        self.assertContains(response, "Передано майбутніх занять: 3")
        expected = deepcopy(before)
        for enrollment in expected["Enrollment"]:
            if enrollment["id"] == self.enrollment.pk:
                enrollment["teacher_id"] = self.new_teacher.pk
        for lesson in expected["Lesson"]:
            if lesson["id"] in (self.future.pk, self.second_future.pk, self.substituted_future.pk):
                lesson["teacher_id"] = self.new_teacher.pk
        self.assertEqual(self.snapshot(), expected)

    def test_future_homework_blocks_whole_transfer_and_single_replacement(self):
        self.add_future_homework()
        for url in (self.course_url, self.lesson_url):
            with self.subTest(url=url):
                self.assert_rejected(url, {"teacher_id": self.new_teacher.pk}, "домашні завдання")

    def test_already_target_teacher_lesson_with_homework_is_not_transferred(self):
        self.future.teacher = self.new_teacher
        self.future.save(update_fields=["teacher"])
        self.add_future_homework()
        before = self.snapshot()
        response = self.client.post(self.course_url, {"teacher_id": self.new_teacher.pk}, follow=True)
        self.assertContains(response, "Передано майбутніх занять: 2")
        self.assertEqual(self.snapshot()["Assignment"], before["Assignment"])
        self.assertEqual(self.snapshot()["Submission"], before["Submission"])

    def test_conflict_on_later_lesson_blocks_whole_transfer(self):
        self.add_target_conflict(self.second_future.starts_at)
        self.assert_rejected(
            self.course_url, {"teacher_id": self.new_teacher.pk}, "Викладач уже має інше заняття"
        )

    def test_cancelled_target_lesson_does_not_block_transfer(self):
        conflict = self.add_target_conflict(self.future.starts_at)
        conflict.status = "cancelled"
        conflict.save(update_fields=["status"])
        response = self.client.post(self.course_url, {"teacher_id": self.new_teacher.pk})
        self.assertRedirects(response, reverse("school_admin_dashboard"))
        self.future.refresh_from_db()
        self.assertEqual(self.future.teacher_id, self.new_teacher.pk)

    def test_transfer_rolls_back_if_saving_a_later_lesson_fails(self):
        before = self.snapshot()
        original_save = Lesson.save

        def fail_second(lesson, *args, **kwargs):
            if lesson.pk == self.second_future.pk:
                raise RuntimeError("Simulated failure")
            return original_save(lesson, *args, **kwargs)

        with patch.object(Lesson, "save", fail_second):
            with self.assertRaises(RuntimeError):
                transfer_enrollment_teacher(self.enrollment.pk, self.new_teacher.pk)
        self.assertEqual(self.snapshot(), before)

    def test_same_teacher_is_a_noop_even_when_future_homework_exists(self):
        self.add_future_homework()
        for url in (self.course_url, self.lesson_url):
            with self.subTest(url=url):
                before = self.snapshot()
                response = self.client.post(url, {"teacher_id": self.teacher.pk}, follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.snapshot(), before)

    def test_single_substitution_keeps_course_and_other_lessons_unchanged(self):
        before = self.snapshot()
        response = self.client.post(self.lesson_url, {"teacher_id": self.substitute.pk})
        self.assertRedirects(response, reverse("school_admin_dashboard"))
        expected = deepcopy(before)
        for lesson in expected["Lesson"]:
            if lesson["id"] == self.future.pk:
                lesson["teacher_id"] = self.substitute.pk
        self.assertEqual(self.snapshot(), expected)

    def test_single_substitution_rejects_busy_teacher(self):
        self.add_target_conflict(self.future.starts_at)
        self.assert_rejected(
            self.lesson_url, {"teacher_id": self.new_teacher.pk}, "Викладач уже має інше заняття"
        )

    def test_past_started_completed_and_cancelled_lessons_cannot_be_reassigned(self):
        for lesson in (self.past, self.past_scheduled, self.in_progress,
                       self.completed_future, self.cancelled_future):
            with self.subTest(lesson=lesson.pk):
                before = self.snapshot()
                response = self.client.post(
                    reverse("assign_lesson_teacher", args=[lesson.pk]),
                    {"teacher_id": self.new_teacher.pk}, follow=True,
                )
                self.assertIn(response.status_code, (200, 404))
                self.assertEqual(self.snapshot(), before)

    def test_inactive_enrollment_cannot_receive_teacher(self):
        for status in ("pending", "cancelled", "rejected"):
            with self.subTest(status=status):
                self.enrollment.status = status
                self.enrollment.save(update_fields=["status"])
                before = self.snapshot()
                response = self.client.post(self.course_url, {"teacher_id": self.new_teacher.pk})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(self.snapshot(), before)

    def test_assignment_endpoints_require_school_admin_and_post(self):
        plain_admin = User.objects.create_user(username="plain_admin", role="admin")
        for user in (self.student, self.teacher, plain_admin):
            self.client.force_login(user)
            for url in (self.course_url, self.lesson_url):
                with self.subTest(user=user.username, url=url):
                    before = self.snapshot()
                    self.assertEqual(self.client.post(url, {"teacher_id": self.new_teacher.pk}).status_code, 403)
                    self.assertEqual(self.snapshot(), before)
        self.client.force_login(self.school_admin)
        for url in (self.course_url, self.lesson_url):
            self.assertEqual(self.client.get(url).status_code, 405)

    def test_assignment_endpoints_require_login_and_csrf(self):
        self.client.logout()
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(self.school_admin)
        for url in (self.course_url, self.lesson_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"teacher_id": self.new_teacher.pk}).status_code, 302)
                self.assertEqual(secure_client.post(url, {"teacher_id": self.new_teacher.pk}).status_code, 403)

    def test_models_reject_student_as_teacher(self):
        self.enrollment.teacher = self.student
        with self.assertRaises(ValidationError):
            self.enrollment.full_clean()
        self.future.teacher = self.student
        with self.assertRaises(ValidationError):
            self.future.full_clean()

    def test_admin_enrollment_and_lesson_forms_reject_student_teacher(self):
        before = self.snapshot()
        for url, data in (
            (reverse("admin:enrollments_enrollment_change", args=[self.enrollment.pk]),
             self.admin_enrollment_data(self.student.pk)),
            (reverse("admin:scheduling_lesson_change", args=[self.future.pk]),
             self.admin_lesson_data(self.future, self.student.pk)),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 200)
                self.assertIn("teacher", response.context["adminform"].form.errors)
                self.assertEqual(self.snapshot(), before)

    def test_admin_course_change_transfers_future_lessons(self):
        response = self.client.post(
            reverse("admin:enrollments_enrollment_change", args=[self.enrollment.pk]),
            self.admin_enrollment_data(self.new_teacher.pk),
        )
        self.assertEqual(response.status_code, 302)
        self.future.refresh_from_db()
        self.enrollment.refresh_from_db()
        self.past.refresh_from_db()
        self.assertEqual(self.enrollment.teacher_id, self.new_teacher.pk)
        self.assertEqual(self.future.teacher_id, self.new_teacher.pk)
        self.assertEqual(self.past.teacher_id, self.teacher.pk)
        self.assertEqual(Submission.objects.get(pk=self.answer.pk).grade, 91)

    def test_admin_can_approve_and_assign_in_the_same_post(self):
        self.enrollment.status = "pending"
        self.enrollment.teacher = None
        self.enrollment.save(update_fields=["status", "teacher"])
        response = self.client.post(
            reverse("admin:enrollments_enrollment_change", args=[self.enrollment.pk]),
            self.admin_enrollment_data(self.new_teacher.pk),
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "approved")
        self.assertEqual(self.enrollment.teacher_id, self.new_teacher.pk)

    def test_admin_transfer_with_homework_or_conflict_changes_nothing(self):
        url = reverse("admin:enrollments_enrollment_change", args=[self.enrollment.pk])
        self.add_target_conflict(self.second_future.starts_at)
        before = self.snapshot()
        response = self.client.post(url, self.admin_enrollment_data(self.new_teacher.pk))
        self.assertContains(response, "Викладач уже має інше заняття")
        self.assertEqual(self.snapshot(), before)
        self.add_future_homework()
        before = self.snapshot()
        response = self.client.post(url, self.admin_enrollment_data(self.new_teacher.pk))
        self.assertContains(response, "домашні завдання")
        self.assertEqual(self.snapshot(), before)

    def test_admin_cannot_reassign_history_or_lesson_with_homework(self):
        self.add_future_homework()
        for lesson in (self.past, self.past_scheduled, self.in_progress,
                       self.completed_future, self.cancelled_future, self.future):
            with self.subTest(lesson=lesson.pk):
                before = self.snapshot()
                response = self.client.post(
                    reverse("admin:scheduling_lesson_change", args=[lesson.pk]),
                    self.admin_lesson_data(lesson, self.new_teacher.pk),
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.non_field_errors())
                self.assertEqual(self.snapshot(), before)

    def test_admin_single_substitution_does_not_change_course_teacher(self):
        response = self.client.post(
            reverse("admin:scheduling_lesson_change", args=[self.future.pk]),
            self.admin_lesson_data(self.future, self.substitute.pk),
        )
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.future.refresh_from_db()
        self.assertEqual(self.enrollment.teacher_id, self.teacher.pk)
        self.assertEqual(self.future.teacher_id, self.substitute.pk)

    def test_admin_cannot_clear_teacher_or_change_course_during_transfer(self):
        url = reverse("admin:enrollments_enrollment_change", args=[self.enrollment.pk])
        for data in (
            self.admin_enrollment_data(""),
            self.admin_enrollment_data(self.new_teacher.pk, student=self.other_student.pk),
        ):
            with self.subTest(data=data):
                before = self.snapshot()
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
                self.assertEqual(self.snapshot(), before)
