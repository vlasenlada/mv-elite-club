from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from enrollments.models import Enrollment
from learning.models import Assignment, Submission
from scheduling.models import Lesson
from users.models import User

from .models import Attendance, GroupMembership, StudyGroup


class GroupLearningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(username="group_teacher", role="teacher")
        cls.other_teacher = User.objects.create_user(username="other_teacher", role="teacher")
        cls.students = [User.objects.create_user(username=f"student_{n}") for n in range(3)]
        cls.school_admin = User.objects.create_superuser(username="admin", email="admin@example.com", password=None, role="admin")
        cls.course = Course.objects.create(
            title="Англійська для початківців", subject="english", description="Existing course",
            lesson_format="individual", price="150.00", is_published=True,
        )
        cls.enrollments = [Enrollment.objects.create(student=s, course=cls.course, teacher=cls.teacher, status="approved") for s in cls.students]
        cls.group = StudyGroup.objects.create(name="English · A", course=cls.course, teacher=cls.teacher)
        cls.members = [GroupMembership.objects.create(
            group=cls.group, enrollment=e, joined_at=timezone.now() - timedelta(days=10),
        ) for e in cls.enrollments[:2]]
        cls.start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        cls.lesson = Lesson(
            group=cls.group, teacher=cls.teacher, starts_at=cls.start,
            ends_at=cls.start + timedelta(hours=1), meeting_url="https://example.com/group-private",
        )
        cls.lesson.full_clean()
        cls.lesson.save()

    def homework(self, lesson=None):
        return Assignment.objects.create(lesson=lesson or self.lesson, title="Group homework", description="Shared group task")

    def group_data(self, group=None):
        return {
            "group": (group or self.group).pk,
            "starts_at": timezone.localtime(self.start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(self.start + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
            "meeting_url": "https://example.com/new-group-meeting",
        }

    def test_legacy_course_supports_both_formats_without_duplicate(self):
        self.assertTrue(self.course.supports_group)
        self.assertTrue(self.course.supports_individual)
        self.assertEqual(Course.objects.filter(title=self.course.title).count(), 1)
        self.assertEqual(self.lesson.course.pk, self.course.pk)

    def test_one_group_lesson_creates_one_attendance_per_student(self):
        self.assertEqual(Lesson.objects.filter(group=self.group).count(), 1)
        self.assertEqual(self.lesson.attendances.count(), 2)
        self.assertEqual(set(self.lesson.attendances.values_list("membership_id", flat=True)), {m.pk for m in self.members})

    def test_lesson_must_have_exactly_one_audience(self):
        for enrollment, group in ((None, None), (self.enrollments[0], self.group)):
            with self.subTest(enrollment=enrollment):
                lesson = Lesson(enrollment=enrollment, group=group, teacher=self.teacher, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
                with self.assertRaises(ValidationError):
                    lesson.full_clean()

    def test_individual_and_group_teacher_conflict(self):
        lesson = Lesson(enrollment=self.enrollments[2], teacher=self.teacher, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        with self.assertRaises(ValidationError) as error:
            lesson.full_clean()
        self.assertIn("Викладач уже має", str(error.exception))

    def test_student_conflict_between_individual_and_group(self):
        lesson = Lesson(enrollment=self.enrollments[0], teacher=self.other_teacher, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        with self.assertRaises(ValidationError) as error:
            lesson.full_clean()
        self.assertIn("Учень уже має", str(error.exception))

    def test_student_conflict_between_different_groups_and_courses(self):
        course = Course.objects.create(title="Math", subject="math", description="Math", lesson_format="group")
        enrollment = Enrollment.objects.create(course=course, student=self.students[0], status="approved")
        group = StudyGroup.objects.create(name="Math", course=course, teacher=self.other_teacher)
        GroupMembership.objects.create(group=group, enrollment=enrollment)
        lesson = Lesson(group=group, teacher=self.other_teacher, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            lesson.full_clean()

    def test_cancelled_group_lesson_does_not_block_time(self):
        self.lesson.status = "cancelled"
        self.lesson.save(update_fields=["status"])
        lesson = Lesson(enrollment=self.enrollments[0], teacher=self.teacher, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        lesson.full_clean()

    def test_joining_existing_group_checks_student_schedule(self):
        Lesson.objects.create(enrollment=self.enrollments[2], teacher=self.other_teacher, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[2])
        self.assertEqual(self.group.memberships.count(), 2)
        self.assertEqual(self.lesson.attendances.count(), 2)

    def test_joining_existing_group_adds_attendance_to_future_lessons(self):
        GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[2])
        self.assertEqual(self.lesson.attendances.count(), 3)

    def test_membership_requires_matching_course_and_approved_student(self):
        self.enrollments[2].status = "pending"
        self.enrollments[2].save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[2])
        other_course = Course.objects.create(title="Other", subject="math", description="Other", lesson_format="group")
        other = Enrollment.objects.create(course=other_course, student=self.students[2], status="approved")
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(group=self.group, enrollment=other)

    def test_student_cannot_be_group_teacher(self):
        with self.assertRaises(ValidationError):
            StudyGroup.objects.create(name="Invalid", course=self.course, teacher=self.students[0])

    def test_one_active_group_per_course_enrollment(self):
        other_group = StudyGroup.objects.create(name="Other group", course=self.course, teacher=self.other_teacher)
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(group=other_group, enrollment=self.enrollments[0])

    def test_student_sees_own_group_but_not_roster_or_peers_answers(self):
        assignment = self.homework()
        Submission.objects.create(assignment=assignment, student=self.students[1], answer_text="PRIVATE PEER ANSWER", grade=27, feedback="PRIVATE PEER FEEDBACK")
        self.client.force_login(self.students[0])
        response = self.client.get(reverse("group_detail", args=[self.group.pk]))
        self.assertContains(response, "Shared group task")
        self.assertNotContains(response, "PRIVATE PEER")
        self.assertNotContains(response, self.students[1].username)
        dashboard = self.client.get(reverse("student_dashboard"))
        self.assertContains(dashboard, self.group.name)
        self.assertContains(dashboard, assignment.title)
        self.assertNotContains(dashboard, "PRIVATE PEER")

    def test_outsider_cannot_read_group_join_or_submit(self):
        assignment = self.homework()
        self.client.force_login(self.students[2])
        for url in (reverse("group_detail", args=[self.group.pk]), reverse("join_lesson", args=[self.lesson.pk]), reverse("submit_homework", args=[assignment.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(reverse("submit_homework", args=[assignment.pk]), {"answer_text": "Injected"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Submission.objects.count(), 0)

    def test_group_meeting_is_protected_by_current_membership(self):
        self.client.force_login(self.students[0])
        self.assertRedirects(self.client.get(reverse("join_lesson", args=[self.lesson.pk])), self.lesson.meeting_url, fetch_redirect_response=False)
        self.members[0].is_active = False
        self.members[0].save(update_fields=["is_active"])
        self.assertEqual(self.client.get(reverse("join_lesson", args=[self.lesson.pk])).status_code, 404)
        response = self.client.get(reverse("student_dashboard"))
        self.assertNotContains(response, reverse("join_lesson", args=[self.lesson.pk]))
        self.assertNotContains(response, self.lesson.meeting_url)

    def test_cancelling_enrollment_ends_membership_without_deleting_history(self):
        past = Lesson.objects.create(group=self.group, teacher=self.teacher, starts_at=timezone.now() - timedelta(days=2), ends_at=timezone.now() - timedelta(days=2) + timedelta(hours=1), status="completed")
        assignment = self.homework(past)
        answer = Submission.objects.create(assignment=assignment, student=self.students[0], answer_text="Historical answer", grade=92)
        self.enrollments[0].status = "cancelled"
        self.enrollments[0].save(update_fields=["status"])
        self.members[0].refresh_from_db()
        self.assertFalse(self.members[0].is_active)
        self.assertIsNotNone(self.members[0].left_at)
        self.assertTrue(past.attendances.filter(membership=self.members[0]).exists())
        self.assertEqual(Submission.objects.get(pk=answer.pk).grade, 92)
        self.client.force_login(self.students[0])
        self.assertEqual(self.client.get(reverse("join_lesson", args=[self.lesson.pk])).status_code, 404)
        response = self.client.get(reverse("group_detail", args=[self.group.pk]))
        self.assertContains(response, "Historical answer")
        self.assertNotContains(response, self.lesson.meeting_url)

    def test_rejoining_preserves_separate_membership_periods(self):
        self.members[0].is_active = False
        self.members[0].save(update_fields=["is_active"])
        new_member = GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[0])
        self.assertNotEqual(new_member.pk, self.members[0].pk)
        self.assertEqual(self.group.memberships.filter(enrollment=self.enrollments[0]).count(), 2)

    def test_new_inactive_membership_does_not_grant_material_access(self):
        self.homework()
        member = GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[2], is_active=False)
        self.assertEqual(member.left_at, member.joined_at)
        self.client.force_login(self.students[2])
        response = self.client.get(reverse("group_detail", args=[self.group.pk]))
        self.assertNotContains(response, "Shared group task")
        self.assertFalse(Lesson.objects.for_student(self.students[2]).exists())

    def test_one_assignment_has_separate_answers_and_grades(self):
        assignment = self.homework()
        for student, text in zip(self.students[:2], ("First answer", "Second answer")):
            self.client.force_login(student)
            self.assertEqual(self.client.post(reverse("submit_homework", args=[assignment.pk]), {"answer_text": text}).status_code, 302)
        self.assertEqual(Assignment.objects.count(), 1)
        self.assertEqual(Submission.objects.count(), 2)
        self.client.force_login(self.teacher)
        for student, grade in zip(self.students[:2], (90, 55)):
            submission = Submission.objects.get(student=student, assignment=assignment)
            response = self.client.post(reverse("review_submission", args=[submission.pk]), {"grade": grade, "feedback": f"Feedback for {student.username}"})
            self.assertEqual(response.status_code, 302)
            submission.refresh_from_db()
            self.assertEqual(submission.grade, grade)
        self.client.force_login(self.students[0])
        page = self.client.get(reverse("group_detail", args=[self.group.pk]))
        self.assertContains(page, "First answer")
        self.assertNotContains(page, "Second answer")
        self.assertNotContains(page, f"Feedback for {self.students[1].username}")

    def test_other_teacher_cannot_review_group_work(self):
        assignment = self.homework()
        answer = Submission.objects.create(assignment=assignment, student=self.students[0], answer_text="Own answer")
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.post(reverse("review_submission", args=[answer.pk]), {"grade": 100}).status_code, 404)
        self.assertEqual(self.client.get(reverse("group_detail", args=[self.group.pk])).status_code, 404)

    def test_teacher_creates_one_common_lesson_through_post(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("group_detail", args=[self.group.pk]), self.group_data())
        self.assertRedirects(response, reverse("group_detail", args=[self.group.pk]))
        self.assertEqual(self.group.lessons.count(), 2)
        self.assertEqual(Lesson.objects.order_by("pk").last().attendances.count(), 2)

    def test_student_and_foreign_teacher_cannot_create_group_lesson(self):
        for user in (self.students[0], self.other_teacher):
            self.client.force_login(user)
            response = self.client.post(reverse("group_detail", args=[self.group.pk]), self.group_data())
            self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.group.lessons.count(), 1)

    def test_teacher_cannot_inject_another_group_in_post(self):
        other = StudyGroup.objects.create(name="Foreign", course=self.course, teacher=self.other_teacher)
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("group_detail", args=[self.group.pk]), self.group_data(other))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(other.lessons.count(), 0)

    def test_attendance_can_be_marked_only_by_lesson_teacher(self):
        record = Attendance.objects.get(lesson=self.lesson, membership=self.members[0])
        url = reverse("update_attendance", args=[record.pk])
        for user, status in ((self.students[0], 403), (self.other_teacher, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.post(url, {"status": "present"}).status_code, status)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(url, {"status": "present", "note": "Worked well"}).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, "present")
        other = Attendance.objects.get(lesson=self.lesson, membership=self.members[1])
        self.assertEqual(other.status, "unmarked")

    def test_attendance_rejects_invalid_status_without_500(self):
        record = Attendance.objects.first()
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("update_attendance", args=[record.pk]), {"status": "invalid"}, follow=True)
        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.status, "unmarked")

    def test_group_teacher_transfer_preserves_history_and_checks_homework(self):
        past = Lesson.objects.create(group=self.group, teacher=self.teacher, starts_at=timezone.now() - timedelta(days=2), ends_at=timezone.now() - timedelta(days=2) + timedelta(hours=1), status="completed")
        self.homework(past)
        self.group.teacher = self.other_teacher
        self.group.save()
        self.lesson.refresh_from_db()
        past.refresh_from_db()
        self.assertEqual(self.lesson.teacher_id, self.other_teacher.pk)
        self.assertEqual(past.teacher_id, self.teacher.pk)
        self.homework()
        self.group.teacher = self.teacher
        with self.assertRaises(ValidationError):
            self.group.save()
        self.group.refresh_from_db()
        self.assertEqual(self.group.teacher_id, self.other_teacher.pk)

    def test_group_teacher_transfer_rejects_schedule_conflict(self):
        Lesson.objects.create(enrollment=self.enrollments[2], teacher=self.other_teacher, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        self.group.teacher = self.other_teacher
        with self.assertRaises(ValidationError):
            self.group.save()
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.teacher_id, self.teacher.pk)

    def test_archived_group_blocks_join_new_lessons_and_assignments(self):
        self.group.is_active = False
        self.group.save(update_fields=["is_active"])
        self.client.force_login(self.students[0])
        self.assertEqual(self.client.get(reverse("join_lesson", args=[self.lesson.pk])).status_code, 404)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(reverse("group_detail", args=[self.group.pk]), self.group_data()).status_code, 403)
        self.assertEqual(self.client.post(reverse("teacher_dashboard"), {"lesson": self.lesson.pk, "title": "New", "description": "Task"}).status_code, 200)
        self.assertEqual(Assignment.objects.count(), 0)

    def test_admin_accepts_group_lesson_and_rejects_wrong_membership(self):
        request = RequestFactory().get("/admin/")
        request.user = self.school_admin
        group_form = admin.site._registry[StudyGroup].get_form(request)
        self.assertFalse(group_form({"name": "Invalid", "course": self.course.pk, "teacher": self.students[0].pk, "is_active": True}).is_valid())
        other_course = Course.objects.create(title="Other", subject="math", description="Other", lesson_format="group")
        other = Enrollment.objects.create(course=other_course, student=self.students[2], status="approved")
        member_form = admin.site._registry[GroupMembership].get_form(request)
        self.assertFalse(member_form({"group": self.group.pk, "enrollment": other.pk, "is_active": True}).is_valid())

    def test_group_homework_can_be_created_in_existing_teacher_dashboard(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("teacher_dashboard"), {"lesson": self.lesson.pk, "title": "Common homework", "description": "One task for all", "due_at": ""})
        self.assertRedirects(response, reverse("teacher_dashboard"))
        assignment = Assignment.objects.get(title="Common homework")
        self.assertEqual(assignment.lesson_id, self.lesson.pk)

    def test_individual_learning_continues_beside_group_learning(self):
        individual = Lesson(enrollment=self.enrollments[0], teacher=self.teacher, starts_at=self.start + timedelta(hours=2), ends_at=self.start + timedelta(hours=3))
        individual.full_clean()
        individual.save()
        assignment = self.homework(individual)
        self.client.force_login(self.students[0])
        self.assertEqual(self.client.post(reverse("submit_homework", args=[assignment.pk]), {"answer_text": "Individual answer"}).status_code, 302)
        self.assertEqual(individual.attendances.count(), 0)
        self.assertEqual(Submission.objects.get(assignment=assignment).student_id, self.students[0].pk)

    def test_admin_creates_group_with_multiple_students_in_one_post(self):
        student = User.objects.create_user(username="new_member")
        enrollment = Enrollment.objects.create(student=student, course=self.course, status="approved")
        self.client.force_login(self.school_admin)
        data = {
            "name": "New group", "course": self.course.pk, "teacher": self.other_teacher.pk, "is_active": "on", "capacity": 2,
            "memberships-TOTAL_FORMS": "2", "memberships-INITIAL_FORMS": "0", "memberships-MIN_NUM_FORMS": "0", "memberships-MAX_NUM_FORMS": "1000",
            "memberships-0-enrollment": self.enrollments[2].pk, "memberships-0-is_active": "on",
            "memberships-1-enrollment": enrollment.pk, "memberships-1-is_active": "on", "_save": "Save",
        }
        response = self.client.post(reverse("admin:classrooms_studygroup_add"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StudyGroup.objects.get(name="New group").memberships.count(), 2)

    def test_admin_new_group_rejects_wrong_course_inline_without_500(self):
        course = Course.objects.create(title="Other course", subject="math", description="Math", lesson_format="group")
        self.client.force_login(self.school_admin)
        response = self.client.post(reverse("admin:classrooms_studygroup_add"), {
            "name": "Invalid group", "course": course.pk, "teacher": self.other_teacher.pk, "is_active": "on", "capacity": 2,
            "memberships-TOTAL_FORMS": "1", "memberships-INITIAL_FORMS": "0", "memberships-MIN_NUM_FORMS": "0", "memberships-MAX_NUM_FORMS": "1000",
            "memberships-0-enrollment": self.enrollments[2].pk, "memberships-0-is_active": "on", "_save": "Save",
        })
        self.assertContains(response, "Група та заявка мають належати одному курсу.")
        self.assertFalse(StudyGroup.objects.filter(name="Invalid group").exists())

    def test_admin_creates_one_group_lesson_with_attendance(self):
        self.client.force_login(self.school_admin)
        start = self.start + timedelta(hours=2)
        end = start + timedelta(hours=1)
        response = self.client.post(reverse("admin:scheduling_lesson_add"), {
            "enrollment": "", "group": self.group.pk, "teacher": self.teacher.pk,
            "starts_at_0": timezone.localtime(start).strftime("%Y-%m-%d"),
            "starts_at_1": timezone.localtime(start).strftime("%H:%M:%S"),
            "ends_at_0": timezone.localtime(end).strftime("%Y-%m-%d"),
            "ends_at_1": timezone.localtime(end).strftime("%H:%M:%S"),
            "status": "scheduled", "meeting_url": "", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.group.lessons.count(), 2)
        self.assertEqual(self.group.lessons.latest("pk").attendances.count(), 2)
