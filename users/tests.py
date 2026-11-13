from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from enrollments.models import Enrollment
from learning.forms import TeacherAssignmentForm
from learning.models import Assignment, Submission
from scheduling.forms import TeacherLessonForm
from scheduling.models import Lesson

from .models import User


INACTIVE_STATUSES = (
    Enrollment.Status.CANCELLED,
    Enrollment.Status.REJECTED,
    Enrollment.Status.PENDING,
)


class InactiveEnrollmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user(username="student")
        cls.teacher = User.objects.create_user(
            username="teacher", role=User.Role.TEACHER
        )
        cls.school_admin = User.objects.create_user(
            username="school_admin", role=User.Role.ADMIN, is_staff=True
        )
        cls.course = Course.objects.create(
            title="English history",
            subject=Course.Subject.ENGLISH,
            description="Course",
            lesson_format=Course.LessonFormat.INDIVIDUAL,
            price="100.00",
        )
        cls.enrollment = Enrollment.objects.create(
            student=cls.student,
            teacher=cls.teacher,
            course=cls.course,
            status=Enrollment.Status.APPROVED,
        )
        cls.start = (timezone.now() + timedelta(days=1)).replace(
            second=0, microsecond=0
        )
        cls.meeting_url = "https://example.com/private-future-meeting"
        cls.future_lesson = Lesson.objects.create(
            enrollment=cls.enrollment,
            teacher=cls.teacher,
            starts_at=cls.start,
            ends_at=cls.start + timedelta(hours=1),
            meeting_url=cls.meeting_url,
        )
        cls.past_lesson = Lesson.objects.create(
            enrollment=cls.enrollment,
            teacher=cls.teacher,
            starts_at=cls.start - timedelta(days=3),
            ends_at=cls.start - timedelta(days=3) + timedelta(hours=1),
            status=Lesson.Status.COMPLETED,
        )
        cls.assignment = Assignment.objects.create(
            lesson=cls.past_lesson,
            title="Archived homework",
            description="Archived task description",
        )
        cls.submission = Submission.objects.create(
            assignment=cls.assignment,
            student=cls.student,
            answer_text="Archived student answer",
            grade=87,
            feedback="Archived teacher feedback",
        )

    def setUp(self):
        self.join_url = reverse("join_lesson", args=[self.future_lesson.pk])
        self.teacher_url = reverse("teacher_dashboard")

    def change_status(self, status):
        self.client.force_login(self.school_admin)
        response = self.client.post(
            reverse("update_enrollment_status", args=[self.enrollment.pk]),
            {"status": status},
        )
        self.assertRedirects(response, reverse("school_admin_dashboard"))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, status)

    def history_snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (Lesson, Assignment, Submission)
        }

    def lesson_data(self):
        return {
            "form_type": "lesson",
            "enrollment": self.enrollment.pk,
            "starts_at": timezone.localtime(self.start).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(
                self.start + timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M"),
            "meeting_url": "https://example.com/new-meeting",
        }

    def test_inactive_dashboard_hides_join_link_and_next_lesson(self):
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(status)
                self.client.force_login(self.student)
                response = self.client.get(reverse("student_dashboard"))
                self.assertNotContains(response, self.meeting_url)
                self.assertNotContains(response, self.join_url)
                self.assertIsNone(response.context["next_lesson"])

    def test_bookmarked_join_url_is_denied_after_status_change(self):
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(Enrollment.Status.APPROVED)
                self.client.force_login(self.student)
                self.assertRedirects(
                    self.client.get(self.join_url),
                    self.meeting_url,
                    fetch_redirect_response=False,
                )
                self.change_status(status)
                self.client.force_login(self.student)
                for method in (self.client.get, self.client.head):
                    response = method(self.join_url)
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn("Location", response)

    def test_approved_dashboard_uses_protected_join_url(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("student_dashboard"))
        self.assertContains(response, self.join_url)
        self.assertNotContains(response, self.meeting_url)
        self.assertEqual(response.context["next_lesson"].pk, self.future_lesson.pk)

    def test_approved_join_redirects_without_caching(self):
        self.client.force_login(self.student)
        response = self.client.get(self.join_url)
        self.assertRedirects(
            response, self.meeting_url, fetch_redirect_response=False
        )
        self.assertIn("no-store", response["Cache-Control"])

    def test_join_requires_login(self):
        response = self.client.get(self.join_url)
        self.assertRedirects(
            response, reverse("login") + "?next=" + self.join_url
        )

    def test_join_rejects_other_student_and_teacher(self):
        other_student = User.objects.create_user(username="other_student")
        for user, expected in ((other_student, 404), (self.teacher, 403)):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.join_url).status_code, expected)

    def test_join_rejects_unscheduled_lessons_and_missing_url(self):
        self.client.force_login(self.student)
        for status in (Lesson.Status.CANCELLED, Lesson.Status.COMPLETED):
            with self.subTest(status=status):
                self.future_lesson.status = status
                self.future_lesson.save(update_fields=["status"])
                self.assertEqual(self.client.get(self.join_url).status_code, 404)
        self.future_lesson.status = Lesson.Status.SCHEDULED
        self.future_lesson.meeting_url = ""
        self.future_lesson.save(update_fields=["status", "meeting_url"])
        self.assertEqual(self.client.get(self.join_url).status_code, 404)

    def test_creation_forms_exclude_inactive_enrollment_and_its_lessons(self):
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(status)
                self.client.force_login(self.teacher)
                response = self.client.get(self.teacher_url)
                self.assertFalse(
                    response.context["lesson_form"].fields["enrollment"].queryset.exists()
                )
                self.assertFalse(
                    response.context["assignment_form"].fields["lesson"].queryset.exists()
                )
                self.assertFalse(TeacherLessonForm(
                    self.lesson_data(), teacher=self.teacher
                ).is_valid())
                self.assertFalse(TeacherAssignmentForm(
                    {"lesson": self.past_lesson.pk, "title": "New", "description": "Task"},
                    teacher=self.teacher,
                ).is_valid())

    def test_direct_post_cannot_create_lesson_for_inactive_enrollment(self):
        snapshot = self.history_snapshot()
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(status)
                self.client.force_login(self.teacher)
                response = self.client.post(self.teacher_url, self.lesson_data())
                self.assertEqual(response.status_code, 200)
                self.assertIn("enrollment", response.context["lesson_form"].errors)
                self.assertEqual(self.history_snapshot(), snapshot)

    def test_direct_post_cannot_create_assignment_for_inactive_enrollment(self):
        snapshot = self.history_snapshot()
        for status in INACTIVE_STATUSES:
            for lesson in (self.future_lesson, self.past_lesson):
                with self.subTest(status=status, lesson=lesson.pk):
                    self.change_status(status)
                    self.client.force_login(self.teacher)
                    response = self.client.post(self.teacher_url, {
                        "lesson": lesson.pk, "title": "New", "description": "Task",
                    })
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("lesson", response.context["assignment_form"].errors)
                    self.assertEqual(self.history_snapshot(), snapshot)

    def test_approved_enrollment_still_allows_lesson_and_assignment_creation(self):
        self.client.force_login(self.teacher)
        data = self.lesson_data()
        # Create an adjacent lesson rather than duplicating the existing slot.
        data["starts_at"] = data["ends_at"]
        data["ends_at"] = timezone.localtime(
            self.start + timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(self.teacher_url, data)
        self.assertRedirects(response, self.teacher_url)
        self.assertEqual(Lesson.objects.count(), 3)
        response = self.client.post(self.teacher_url, {
            "lesson": self.past_lesson.pk, "title": "New", "description": "Task",
        })
        self.assertRedirects(response, self.teacher_url)
        self.assertEqual(Assignment.objects.count(), 2)

    def test_status_changes_preserve_history_for_student_and_teacher(self):
        snapshot = self.history_snapshot()
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(status)
                self.assertEqual(self.history_snapshot(), snapshot)
                self.client.force_login(self.student)
                response = self.client.get(reverse("student_dashboard"))
                self.assertContains(response, "Archived homework")
                self.assertContains(response, "Archived teacher feedback")
                self.assertEqual(
                    list(response.context["graded_submissions"].values_list("grade", flat=True)),
                    [87],
                )
                self.assertIn(self.past_lesson, response.context["lessons"])
                self.client.force_login(self.teacher)
                response = self.client.get(self.teacher_url)
                self.assertContains(response, "Archived homework")
                self.assertContains(response, "Archived student answer")
                self.assertIn(self.past_lesson, response.context["past_lessons"])

    def test_student_cannot_resubmit_archived_homework(self):
        snapshot = self.history_snapshot()
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(status)
                self.client.force_login(self.student)
                url = reverse("submit_homework", args=[self.assignment.pk])
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(
                    self.client.post(url, {"answer_text": "Changed"}).status_code, 404
                )
                self.assertEqual(self.history_snapshot(), snapshot)

    def test_new_assignment_model_validation_rejects_inactive_enrollment(self):
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(status)
                assignment = Assignment(
                    lesson=Lesson.objects.get(pk=self.past_lesson.pk),
                    title="New", description="Task",
                )
                with self.assertRaises(ValidationError) as caught:
                    assignment.full_clean()
                self.assertIn(NON_FIELD_ERRORS, caught.exception.message_dict)

    def test_existing_assignment_remains_valid_after_cancellation(self):
        self.change_status(Enrollment.Status.CANCELLED)
        self.assignment.refresh_from_db()
        self.assignment.full_clean()

    def test_admin_form_cannot_create_assignment_for_inactive_enrollment(self):
        request = RequestFactory().get("/admin/learning/assignment/add/")
        request.user = self.school_admin
        form_class = admin.site._registry[Assignment].get_form(request)
        for status in INACTIVE_STATUSES:
            with self.subTest(status=status):
                self.change_status(status)
                form = form_class({
                    "lesson": self.past_lesson.pk, "title": "New", "description": "Task",
                })
                self.assertFalse(form.is_valid())
                self.assertTrue(form.non_field_errors())
