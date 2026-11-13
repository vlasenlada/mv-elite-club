from datetime import timedelta

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from enrollments.models import Enrollment
from learning.models import Assignment, Submission
from users.models import User

from .forms import TeacherLessonEditForm, TeacherLessonForm
from .models import Lesson


TEACHER_ERROR = (
    "Викладач уже має інше заняття в цей час. Оберіть інший час або викладача."
)
STUDENT_ERROR = "Учень уже має інше заняття в цей час. Оберіть інший час."


class ScheduleConflictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(username="teacher", role="teacher")
        cls.other_teacher = User.objects.create_user(username="other_teacher", role="teacher")
        cls.student = User.objects.create_user(username="student")
        cls.other_student = User.objects.create_user(username="other_student")
        cls.school_admin = User.objects.create_superuser(
            username="school_admin", email="admin@example.com", password=None,
            role=User.Role.ADMIN,
        )
        courses = [Course.objects.create(
            title=title, subject="english", description="Course",
            lesson_format="individual", price="100.00",
        ) for title in ("English", "Advanced English")]
        cls.enrollment, cls.teacher_enrollment, cls.student_enrollment, cls.other_enrollment = [
            Enrollment.objects.create(
                student=student, teacher=teacher, course=course,
                status=Enrollment.Status.APPROVED,
            ) for student, teacher, course in (
                (cls.student, cls.teacher, courses[0]),
                (cls.other_student, cls.teacher, courses[0]),
                (cls.student, cls.other_teacher, courses[1]),
                (cls.other_student, cls.other_teacher, courses[1]),
            )
        ]
        cls.start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        cls.lesson = Lesson.objects.create(
            enrollment=cls.enrollment, teacher=cls.teacher,
            starts_at=cls.start, ends_at=cls.start + timedelta(hours=1),
        )
        cls.assignment = Assignment.objects.create(
            lesson=cls.lesson, title="Existing homework", description="Task"
        )
        cls.submission = Submission.objects.create(
            assignment=cls.assignment, student=cls.student,
            answer_text="Existing answer", grade=90, feedback="Existing feedback",
        )

    def candidate(self, enrollment=None, start=0, end=60, **kwargs):
        enrollment = enrollment or self.teacher_enrollment
        return Lesson(
            enrollment=enrollment, teacher=enrollment.teacher,
            starts_at=self.start + timedelta(minutes=start),
            ends_at=self.start + timedelta(minutes=end), **kwargs,
        )

    def assert_conflict(self, lesson, code):
        with self.assertRaises(ValidationError) as caught:
            lesson.full_clean()
        errors = caught.exception.error_dict[NON_FIELD_ERRORS]
        self.assertIn(code, [error.code for error in errors])

    def data(self, lesson):
        return {
            "enrollment": lesson.enrollment_id,
            "starts_at": timezone.localtime(lesson.starts_at).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(lesson.ends_at).strftime("%Y-%m-%dT%H:%M"),
            "meeting_url": lesson.meeting_url,
        }

    def admin_data(self, lesson):
        return {
            "enrollment": lesson.enrollment_id,
            "teacher": lesson.teacher_id or "",
            "starts_at_0": timezone.localtime(lesson.starts_at).strftime("%Y-%m-%d"),
            "starts_at_1": timezone.localtime(lesson.starts_at).strftime("%H:%M:%S"),
            "ends_at_0": timezone.localtime(lesson.ends_at).strftime("%Y-%m-%d"),
            "ends_at_1": timezone.localtime(lesson.ends_at).strftime("%H:%M:%S"),
            "status": lesson.status, "meeting_url": lesson.meeting_url, "_save": "Save",
        }

    def snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (Lesson, Assignment, Submission)
        }

    def test_teacher_conflict_for_all_overlap_shapes(self):
        for start, end in ((0, 60), (-30, 30), (30, 90), (-30, 90), (15, 45)):
            with self.subTest(start=start, end=end):
                self.assert_conflict(self.candidate(start=start, end=end), "teacher_schedule_conflict")

    def test_student_conflict_across_courses_and_teachers(self):
        self.assert_conflict(self.candidate(self.student_enrollment), "student_schedule_conflict")

    def test_different_teacher_and_student_can_share_time(self):
        self.candidate(self.other_enrollment).full_clean()

    def test_adjacent_lessons_before_and_after_are_allowed(self):
        for start, end in ((-60, 0), (60, 120)):
            with self.subTest(start=start):
                self.candidate(start=start, end=end).full_clean()

    def test_cancelled_existing_lesson_does_not_block_teacher_or_student(self):
        self.lesson.status = Lesson.Status.CANCELLED
        self.lesson.save(update_fields=["status"])
        self.candidate().full_clean()
        self.candidate(self.student_enrollment).full_clean()

    def test_cancelled_candidate_can_overlap(self):
        self.candidate(status=Lesson.Status.CANCELLED).full_clean()

    def test_completed_lesson_still_blocks_its_time(self):
        self.lesson.status = Lesson.Status.COMPLETED
        self.lesson.save(update_fields=["status"])
        self.assert_conflict(self.candidate(), "teacher_schedule_conflict")

    def test_edit_excludes_itself(self):
        self.lesson.full_clean()
        self.lesson.ends_at += timedelta(minutes=10)
        self.lesson.full_clean()

    def test_student_conflict_is_checked_without_teacher(self):
        candidate = self.candidate(self.student_enrollment)
        candidate.teacher = None
        self.assert_conflict(candidate, "student_schedule_conflict")

    def test_creation_form_checks_teacher_before_view_assigns_it(self):
        form = TeacherLessonForm(self.data(self.candidate()), teacher=self.teacher)
        self.assertFalse(form.is_valid())
        self.assertIn(TEACHER_ERROR, form.non_field_errors())

    def test_incomplete_form_displays_errors_without_crashing(self):
        form = TeacherLessonForm(
            {"enrollment": self.enrollment.pk}, teacher=self.teacher
        )
        self.assertFalse(form.is_valid())
        self.assertIn("starts_at", form.errors)
        self.assertIn("ends_at", form.errors)

    def test_direct_creation_post_rejects_teacher_and_student_conflicts(self):
        before = self.snapshot()
        for enrollment, teacher, message in (
            (self.teacher_enrollment, self.teacher, TEACHER_ERROR),
            (self.student_enrollment, self.other_teacher, STUDENT_ERROR),
        ):
            with self.subTest(message=message):
                self.client.force_login(teacher)
                data = self.data(self.candidate(enrollment))
                data["form_type"] = "lesson"
                response = self.client.post(reverse("teacher_dashboard"), data)
                self.assertContains(response, message)
                self.assertEqual(self.snapshot(), before)

    def test_direct_reschedule_post_rejects_conflict_and_preserves_history(self):
        target = self.candidate(start=120, end=180)
        target.save()
        before = self.snapshot()
        self.client.force_login(self.teacher)
        data = self.data(self.lesson)
        data["starts_at"] = self.data(target)["starts_at"]
        data["ends_at"] = self.data(target)["ends_at"]
        response = self.client.post(reverse("edit_teacher_lesson", args=[self.lesson.pk]), data)
        self.assertContains(response, TEACHER_ERROR)
        self.assertEqual(self.snapshot(), before)

    def test_edit_form_rejects_student_conflict(self):
        target = self.candidate(self.student_enrollment, start=120, end=180)
        target.save()
        data = self.data(target)
        form = TeacherLessonEditForm(data, instance=self.lesson)
        self.assertFalse(form.is_valid())
        self.assertIn(STUDENT_ERROR, form.non_field_errors())

    def test_direct_reschedule_to_free_time_succeeds(self):
        self.client.force_login(self.teacher)
        data = self.data(self.candidate(self.enrollment, start=60, end=120))
        response = self.client.post(reverse("edit_teacher_lesson", args=[self.lesson.pk]), data)
        self.assertRedirects(response, reverse("teacher_dashboard"))
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.starts_at, self.start + timedelta(hours=1))
        self.assertEqual(Submission.objects.get(pk=self.submission.pk).grade, 90)

    def test_restoring_cancelled_lesson_checks_teacher_and_student(self):
        for enrollment, teacher, message in (
            (self.teacher_enrollment, self.teacher, TEACHER_ERROR),
            (self.student_enrollment, self.other_teacher, STUDENT_ERROR),
        ):
            with self.subTest(message=message):
                candidate = self.candidate(enrollment, status=Lesson.Status.CANCELLED)
                candidate.save()
                before = self.snapshot()
                self.client.force_login(teacher)
                response = self.client.post(
                    reverse("update_lesson_status", args=[candidate.pk]),
                    {"status": Lesson.Status.SCHEDULED}, follow=True,
                )
                self.assertContains(response, message)
                self.assertEqual(self.snapshot(), before)

    def test_cancelling_lesson_is_allowed_even_with_legacy_conflict(self):
        self.candidate().save()
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("update_lesson_status", args=[self.lesson.pk]),
            {"status": Lesson.Status.CANCELLED},
        )
        self.assertRedirects(response, reverse("teacher_dashboard"))
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.status, Lesson.Status.CANCELLED)

    def test_teacher_reassignment_rejects_busy_teacher(self):
        target = self.candidate(self.other_enrollment)
        target.save()
        before = self.snapshot()
        self.client.force_login(self.school_admin)
        response = self.client.post(
            reverse("assign_lesson_teacher", args=[target.pk]),
            {"teacher_id": self.teacher.pk}, follow=True,
        )
        self.assertContains(response, TEACHER_ERROR)
        self.assertEqual(self.snapshot(), before)

    def test_teacher_reassignment_to_free_teacher_succeeds(self):
        target = self.candidate(self.other_enrollment, start=120, end=180)
        target.save()
        self.client.force_login(self.school_admin)
        response = self.client.post(
            reverse("assign_lesson_teacher", args=[target.pk]),
            {"teacher_id": self.teacher.pk},
        )
        self.assertRedirects(response, reverse("school_admin_dashboard"))
        target.refresh_from_db()
        self.assertEqual(target.teacher_id, self.teacher.pk)

    def test_admin_create_rejects_conflicting_lesson(self):
        self.client.force_login(self.school_admin)
        before = self.snapshot()
        response = self.client.post(
            reverse("admin:scheduling_lesson_add"), self.admin_data(self.candidate())
        )
        self.assertContains(response, TEACHER_ERROR)
        self.assertEqual(self.snapshot(), before)

    def test_admin_change_checks_new_teacher(self):
        target = self.candidate(self.other_enrollment)
        target.save()
        before = self.snapshot()
        self.client.force_login(self.school_admin)
        data = self.admin_data(self.lesson)
        data["teacher"] = self.other_teacher.pk
        response = self.client.post(
            reverse("admin:scheduling_lesson_change", args=[self.lesson.pk]), data
        )
        self.assertContains(response, TEACHER_ERROR)
        self.assertEqual(self.snapshot(), before)

    def test_admin_reschedule_checks_student_conflict(self):
        target = self.candidate(self.student_enrollment, start=120, end=180)
        target.save()
        before = self.snapshot()
        self.client.force_login(self.school_admin)
        data = self.admin_data(self.lesson)
        target_data = self.admin_data(target)
        for field in ("starts_at_0", "starts_at_1", "ends_at_0", "ends_at_1"):
            data[field] = target_data[field]
        response = self.client.post(
            reverse("admin:scheduling_lesson_change", args=[self.lesson.pk]), data
        )
        self.assertContains(response, STUDENT_ERROR)
        self.assertEqual(self.snapshot(), before)

    def test_admin_restore_cancelled_lesson_rejects_conflict(self):
        target = self.candidate(status=Lesson.Status.CANCELLED)
        target.save()
        before = self.snapshot()
        self.client.force_login(self.school_admin)
        data = self.admin_data(target)
        data["status"] = Lesson.Status.SCHEDULED
        response = self.client.post(
            reverse("admin:scheduling_lesson_change", args=[target.pk]), data
        )
        self.assertContains(response, TEACHER_ERROR)
        self.assertEqual(self.snapshot(), before)

    def test_admin_can_save_unchanged_lesson_without_self_conflict(self):
        self.client.force_login(self.school_admin)
        response = self.client.post(
            reverse("admin:scheduling_lesson_change", args=[self.lesson.pk]),
            self.admin_data(self.lesson),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Submission.objects.get(pk=self.submission.pk).grade, 90)
