from datetime import timedelta

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from enrollments.models import Enrollment
from learning.models import Assignment, Submission
from users.models import User

from .forms import TeacherLessonEditForm, TeacherMeetingLinkForm
from .models import Lesson


ENROLLMENT_ERROR = (
    "Заявка учня не підтверджена або скасована. "
    "Створення та редагування заняття недоступні."
)
UNAPPROVED_STATUSES = (
    Enrollment.Status.CANCELLED,
    Enrollment.Status.REJECTED,
    Enrollment.Status.PENDING,
)


class LessonEnrollmentValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(
            username="teacher", role=User.Role.TEACHER
        )
        cls.student = User.objects.create_user(username="student")
        cls.course = Course.objects.create(
            title="English",
            subject=Course.Subject.ENGLISH,
            description="English lessons",
            lesson_format=Course.LessonFormat.INDIVIDUAL,
            price="100.00",
        )
        cls.enrollment = Enrollment.objects.create(
            student=cls.student,
            course=cls.course,
            teacher=cls.teacher,
            status=Enrollment.Status.APPROVED,
        )
        cls.original_start = (timezone.now() + timedelta(days=1)).replace(
            second=0, microsecond=0
        )
        cls.original_end = cls.original_start + timedelta(hours=1)
        cls.original_url = "https://example.com/original"
        cls.lesson = Lesson.objects.create(
            enrollment=cls.enrollment,
            teacher=cls.teacher,
            starts_at=cls.original_start,
            ends_at=cls.original_end,
            meeting_url=cls.original_url,
        )
        cls.assignment = Assignment.objects.create(
            lesson=cls.lesson, title="Homework", description="Task"
        )
        cls.submission = Submission.objects.create(
            assignment=cls.assignment,
            student=cls.student,
            answer_text="Existing answer",
            grade=90,
        )

    def setUp(self):
        self.client.force_login(self.teacher)
        self.edit_url = reverse("edit_teacher_lesson", args=[self.lesson.pk])
        self.link_url = reverse("update_meeting_link", args=[self.lesson.pk])
        self.new_start = self.original_start + timedelta(days=1)
        self.new_end = self.original_end + timedelta(days=1)
        self.edit_data = {
            "starts_at": timezone.localtime(self.new_start).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(self.new_end).strftime("%Y-%m-%dT%H:%M"),
            "meeting_url": "https://example.com/updated",
        }

    def set_enrollment_status(self, status):
        self.enrollment.status = status
        self.enrollment.save(update_fields=["status"])
        self.lesson.refresh_from_db()

    def assert_existing_data_unchanged(self):
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.starts_at, self.original_start)
        self.assertEqual(self.lesson.ends_at, self.original_end)
        self.assertEqual(self.lesson.meeting_url, self.original_url)
        self.assertEqual(self.lesson.status, Lesson.Status.SCHEDULED)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.lesson_id, self.lesson.pk)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answer_text, "Existing answer")
        self.assertEqual(self.submission.grade, 90)

    def test_model_reports_unapproved_enrollment_as_non_field_error(self):
        for status in UNAPPROVED_STATUSES:
            with self.subTest(status=status):
                self.set_enrollment_status(status)
                with self.assertRaises(ValidationError) as caught:
                    self.lesson.full_clean()
                self.assertEqual(
                    caught.exception.message_dict,
                    {NON_FIELD_ERRORS: [ENROLLMENT_ERROR]},
                )

    def test_edit_form_rejects_unapproved_enrollment_without_exception(self):
        for status in UNAPPROVED_STATUSES:
            with self.subTest(status=status):
                self.set_enrollment_status(status)
                form = TeacherLessonEditForm(self.edit_data, instance=self.lesson)
                self.assertFalse(form.is_valid())
                self.assertIn(ENROLLMENT_ERROR, form.non_field_errors())

    def test_link_form_rejects_unapproved_enrollment_without_exception(self):
        for status in UNAPPROVED_STATUSES:
            with self.subTest(status=status):
                self.set_enrollment_status(status)
                form = TeacherMeetingLinkForm(
                    {"meeting_url": "https://example.com/updated"},
                    instance=self.lesson,
                )
                self.assertFalse(form.is_valid())
                self.assertIn(ENROLLMENT_ERROR, form.non_field_errors())

    def test_edit_post_displays_reason_and_preserves_existing_data(self):
        for status in UNAPPROVED_STATUSES:
            with self.subTest(status=status):
                self.set_enrollment_status(status)
                response = self.client.post(self.edit_url, self.edit_data)
                self.assertContains(response, ENROLLMENT_ERROR)
                self.assertTemplateUsed(response, "users/edit_teacher_lesson.html")
                self.assert_existing_data_unchanged()

    def test_link_post_displays_reason_and_preserves_existing_data(self):
        for status in UNAPPROVED_STATUSES:
            with self.subTest(status=status):
                self.set_enrollment_status(status)
                response = self.client.post(
                    self.link_url,
                    {"meeting_url": "https://example.com/updated"},
                    follow=True,
                )
                self.assertRedirects(response, reverse("teacher_dashboard"))
                self.assertContains(response, ENROLLMENT_ERROR)
                self.assert_existing_data_unchanged()

    def test_clearing_link_is_also_forbidden_after_cancellation(self):
        self.set_enrollment_status(Enrollment.Status.CANCELLED)
        response = self.client.post(self.link_url, {"meeting_url": ""}, follow=True)
        self.assertContains(response, ENROLLMENT_ERROR)
        self.assert_existing_data_unchanged()

    def test_edit_page_remains_available_after_cancellation(self):
        self.set_enrollment_status(Enrollment.Status.CANCELLED)
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/edit_teacher_lesson.html")

    def test_approved_enrollment_allows_time_and_link_edit(self):
        response = self.client.post(self.edit_url, self.edit_data)
        self.assertRedirects(response, reverse("teacher_dashboard"))
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.starts_at, self.new_start)
        self.assertEqual(self.lesson.ends_at, self.new_end)
        self.assertEqual(self.lesson.meeting_url, self.edit_data["meeting_url"])

    def test_approved_enrollment_allows_link_update(self):
        response = self.client.post(
            self.link_url, {"meeting_url": "https://example.com/updated"}
        )
        self.assertRedirects(response, reverse("teacher_dashboard"))
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.meeting_url, "https://example.com/updated")

    def test_approved_enrollment_allows_link_clearing(self):
        response = self.client.post(self.link_url, {"meeting_url": ""})
        self.assertRedirects(response, reverse("teacher_dashboard"))
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.meeting_url, "")

    def test_invalid_url_still_displays_url_error(self):
        response = self.client.post(
            self.link_url, {"meeting_url": "not-a-url"}, follow=True
        )
        self.assertContains(
            response, "Не вдалося зберегти посилання. Перевірте URL."
        )
        self.assert_existing_data_unchanged()

    def test_invalid_time_interval_is_still_rejected(self):
        self.edit_data["ends_at"] = self.edit_data["starts_at"]
        response = self.client.post(self.edit_url, self.edit_data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("ends_at", response.context["form"].errors)
        self.assert_existing_data_unchanged()

    def test_other_teacher_cannot_edit_lesson_or_link(self):
        other_teacher = User.objects.create_user(
            username="other_teacher", role=User.Role.TEACHER
        )
        self.client.force_login(other_teacher)
        self.set_enrollment_status(Enrollment.Status.CANCELLED)
        for url in (self.edit_url, self.link_url):
            with self.subTest(url=url):
                response = self.client.post(url, self.edit_data)
                self.assertEqual(response.status_code, 404)
        self.assert_existing_data_unchanged()
