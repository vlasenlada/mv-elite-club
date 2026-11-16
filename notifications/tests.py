from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from classrooms.models import GroupMembership, StudyGroup
from enrollments.models import Enrollment
from learning.models import Assignment, Submission
from scheduling.models import Lesson
from users.models import User
from courses.models import Course

from .models import EventCursor, Notification


class NotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.get(slug="english-primary")
        cls.student = User.objects.create_user(username="notification_student")
        cls.other = User.objects.create_user(username="notification_other")
        cls.teacher = User.objects.create_user(username="notification_teacher", role="teacher")
        cls.second_teacher = User.objects.create_user(username="notification_second_teacher", role="teacher")
        cls.admin = User.objects.create_superuser(username="notification_admin", email="admin@example.invalid", password=None, role="admin")
        cls.enrollment = Enrollment.objects.create(student=cls.student, course=cls.course)

    def approve(self, enrollment=None, teacher=None):
        enrollment = enrollment or self.enrollment
        enrollment.status = "approved"
        enrollment.teacher = teacher or self.teacher
        enrollment.save(update_fields=["status", "teacher"])
        return enrollment

    def lesson(self, enrollment=None, teacher=None, **overrides):
        start = (timezone.now() + timedelta(days=3)).replace(second=0, microsecond=0)
        return Lesson.objects.create(enrollment=enrollment or self.enrollment, teacher=teacher or self.teacher,
            starts_at=start, ends_at=start + timedelta(hours=1), **overrides)

    def homework(self):
        self.approve()
        lesson = self.lesson()
        return Assignment.objects.create(lesson=lesson, title="Персональна практика", description="Приватна умова")

    def notifications(self, user, kind=None):
        qs = Notification.objects.filter(recipient=user)
        return qs.filter(kind=kind) if kind else qs

    def group(self):
        group = StudyGroup.objects.create(course=self.course, teacher=self.teacher, name="Навчальна група", capacity=8)
        enrollments = [Enrollment.objects.create(student=user, course=self.course, status="approved", requested_format="group")
                       for user in (self.other, User.objects.create_user(username="notification_groupmate"))]
        for enrollment in enrollments:
            GroupMembership.objects.create(group=group, enrollment=enrollment)
        start = timezone.now() + timedelta(days=4)
        lesson = Lesson.objects.create(group=group, teacher=self.teacher, starts_at=start, ends_at=start + timedelta(hours=1))
        return group, enrollments, lesson

    def test_pending_application_does_not_create_notification(self):
        self.assertEqual(Notification.objects.count(), 0)
        self.enrollment.save()
        self.assertEqual(Notification.objects.count(), 0)

    def test_approval_rejection_and_teacher_assignment_notify_only_recipients(self):
        self.approve()
        self.assertEqual(self.notifications(self.student, "enrollment_status").count(), 1)
        self.assertEqual(self.notifications(self.teacher, "student_assigned").count(), 1)
        self.assertFalse(self.notifications(self.other).exists())
        self.enrollment.status = "rejected"
        self.enrollment.save(update_fields=["status"])
        self.assertEqual(self.notifications(self.student, "enrollment_status").count(), 2)
        self.assertIn("відхилено", self.notifications(self.student).first().title)

    def test_admin_http_approval_uses_same_notification_path(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("update_enrollment_status", args=[self.enrollment.pk]), {"status": "approved"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.notifications(self.student).count(), 1)
        self.client.post(reverse("update_enrollment_status", args=[self.enrollment.pk]), {"status": "approved"})
        self.assertEqual(self.notifications(self.student).count(), 1)

    def test_noop_and_unsaved_excluded_fields_do_not_duplicate(self):
        self.approve()
        baseline = Notification.objects.count()
        self.enrollment.status = "rejected"
        self.enrollment.save(update_fields=["teacher"])
        self.assertEqual(Notification.objects.count(), baseline)
        self.enrollment.refresh_from_db()
        self.enrollment.save()
        self.assertEqual(Notification.objects.count(), baseline)

    def test_genuine_repeat_transition_after_intermediate_change_is_a_new_event(self):
        self.approve()
        self.enrollment.status = "rejected"
        self.enrollment.save()
        self.enrollment.status = "approved"
        self.enrollment.save()
        self.assertEqual(self.notifications(self.student, "enrollment_status").count(), 3)
        self.assertEqual(len(set(self.notifications(self.student).values_list("event_key", flat=True))), 3)

    def test_assignment_to_new_teacher_notifies_new_teacher_without_student_private_text(self):
        self.approve()
        self.enrollment.teacher = self.second_teacher
        self.enrollment.save(update_fields=["teacher"])
        row = self.notifications(self.second_teacher, "student_assigned").get()
        self.assertNotIn(self.student.username, row.text)
        self.assertEqual(row.destination(), reverse("teacher_dashboard"))

    def test_new_lesson_notifies_student_and_teacher(self):
        self.approve()
        lesson = self.lesson(meeting_url="https://meet.example.invalid/private-meeting-token")
        self.assertEqual(self.notifications(self.student, "lesson_created").count(), 1)
        self.assertEqual(self.notifications(self.teacher, "lesson_assigned").count(), 1)
        self.assertFalse(self.notifications(self.other, "lesson_created").exists())
        row = self.notifications(self.teacher, "lesson_assigned").get()
        self.assertEqual(row.destination(), reverse("lesson_detail", args=[lesson.pk]))
        state = EventCursor.objects.get(key=f"scheduling.lesson:{lesson.pk}").state
        self.assertNotIn(lesson.meeting_url, str(state))
        self.assertFalse(Notification.objects.filter(text__contains=lesson.meeting_url).exists())

    def test_lesson_reschedule_link_change_and_cancel_each_create_one_update(self):
        self.approve()
        lesson = self.lesson()
        lesson.starts_at += timedelta(hours=2)
        lesson.ends_at += timedelta(hours=2)
        lesson.save(update_fields=["starts_at", "ends_at"])
        self.assertEqual(self.notifications(self.student, "lesson_changed").count(), 1)
        lesson.save()
        self.assertEqual(self.notifications(self.student, "lesson_changed").count(), 1)
        lesson.meeting_url = "https://meet.example.invalid/new-private-link"
        lesson.save(update_fields=["meeting_url"])
        self.assertEqual(self.notifications(self.student, "lesson_changed").count(), 2)
        lesson.status = "cancelled"
        lesson.save(update_fields=["status"])
        self.assertEqual(self.notifications(self.student, "lesson_cancelled").count(), 1)

    def test_http_meeting_link_update_notifies_and_repeat_does_not_duplicate(self):
        self.approve()
        lesson = self.lesson()
        self.client.force_login(self.teacher)
        for _ in range(2):
            self.assertEqual(self.client.post(reverse("update_meeting_link", args=[lesson.pk]),
                {"meeting_url": "https://meet.example.invalid/new-link"}).status_code, 302)
        self.assertEqual(self.notifications(self.student, "lesson_changed").count(), 1)

    def test_teacher_replacement_and_stale_link_do_not_expose_new_teachers_data(self):
        self.approve()
        lesson = self.lesson()
        old = self.notifications(self.teacher, "lesson_assigned").get()
        lesson.teacher = self.second_teacher
        lesson.save(update_fields=["teacher"])
        self.assertEqual(self.notifications(self.second_teacher, "lesson_assigned").count(), 1)
        self.assertEqual(self.notifications(self.teacher, "lesson_reassigned").count(), 1)
        self.assertEqual(old.destination(), reverse("lesson_calendar"))
        self.assertNotIn(self.second_teacher.username, self.notifications(self.teacher).first().text)

    def test_inactive_enrollment_does_not_receive_future_lesson_updates(self):
        self.approve()
        lesson = self.lesson()
        self.enrollment.status = "cancelled"
        self.enrollment.save(update_fields=["status"])
        count = self.notifications(self.student).count()
        lesson.meeting_url = "https://meet.example.invalid/after-cancellation"
        lesson.save(update_fields=["meeting_url"])
        self.assertEqual(self.notifications(self.student).count(), count)

    def test_new_homework_links_to_own_assignment_and_noops_do_not_duplicate(self):
        assignment = self.homework()
        row = self.notifications(self.student, "homework_created").get()
        self.assertEqual(row.destination(), reverse("submit_homework", args=[assignment.pk]))
        assignment.save()
        self.assertEqual(self.notifications(self.student, "homework_created").count(), 1)
        self.enrollment.status = "cancelled"
        self.enrollment.save()
        self.assertEqual(row.destination(), reverse("student_dashboard") + "#my-homework")

    def test_submission_and_resubmission_notify_teacher_but_grade_only_notifies_owner(self):
        assignment = self.homework()
        submission = Submission.objects.create(assignment=assignment, student=self.student, answer_text="Приватна відповідь")
        self.assertEqual(self.notifications(self.teacher, "answer_received").count(), 1)
        submission.save()
        self.assertEqual(self.notifications(self.teacher, "answer_received").count(), 1)
        submission.answer_text = "Нова приватна відповідь"
        submission.save(update_fields=["answer_text"])
        self.assertEqual(self.notifications(self.teacher, "answer_received").count(), 2)
        submission.grade = 0
        submission.feedback = "Особистий конфіденційний коментар"
        submission.save(update_fields=["grade", "feedback"])
        self.assertEqual(self.notifications(self.student, "work_reviewed").count(), 1)
        self.assertFalse(self.notifications(self.other, "work_reviewed").exists())
        self.assertEqual(self.notifications(self.teacher, "answer_received").count(), 2)
        submission.save()
        self.assertEqual(self.notifications(self.student, "work_reviewed").count(), 1)
        for row in Notification.objects.all():
            self.assertNotIn("Приватна", row.text)
            self.assertNotIn("конфіденційний", row.text)

    def test_invalid_foreign_submission_does_not_generate_notification(self):
        assignment = self.homework()
        count = Notification.objects.count()
        Submission.objects.create(assignment=assignment, student=self.other, answer_text="Не належить учаснику", grade=90)
        self.assertEqual(Notification.objects.count(), count)

    def test_group_lesson_and_homework_notify_members_only_without_roster(self):
        group, enrollments, lesson = self.group()
        for enrollment in enrollments:
            self.assertEqual(self.notifications(enrollment.student, "lesson_created").count(), 1)
        self.assertFalse(self.notifications(self.student, "lesson_created").exists())
        assignment = Assignment.objects.create(lesson=lesson, title="Спільне завдання", description="Умова")
        for enrollment in enrollments:
            self.assertEqual(self.notifications(enrollment.student, "homework_created").count(), 1)
        for row in self.notifications(self.other):
            self.assertNotIn(enrollments[1].student.username, row.text)
        first = Submission.objects.create(assignment=assignment, student=self.other, answer_text="Особиста відповідь", grade=91)
        self.assertEqual(self.notifications(self.other, "work_reviewed").count(), 1)
        self.assertFalse(self.notifications(enrollments[1].student, "work_reviewed").exists())

    def test_late_group_admission_gets_existing_future_schedule_without_old_members_duplicates(self):
        group, enrollments, lesson = self.group()
        assignment = Assignment.objects.create(lesson=lesson, title="Підготовлене завдання", description="Умова")
        baseline = self.notifications(self.other, "lesson_created").count()
        enrollment = self.enrollment
        enrollment.status = "approved"
        enrollment.requested_format = "group"
        enrollment.save(update_fields=["status", "requested_format"])
        member = GroupMembership.objects.create(group=group, enrollment=enrollment)
        self.assertEqual(self.notifications(self.student, "lesson_created").count(), 1)
        self.assertEqual(self.notifications(self.student, "homework_created").count(), 1)
        self.assertEqual(self.notifications(self.other, "lesson_created").count(), baseline)
        member.save()
        self.assertEqual(self.notifications(self.student, "lesson_created").count(), 1)
        self.assertEqual(self.notifications(self.student, "homework_created").count(), 1)

    def test_departed_member_is_not_notified_of_future_group_changes(self):
        group, enrollments, lesson = self.group()
        member = GroupMembership.objects.get(enrollment=enrollments[0])
        member.is_active = False
        member.save(update_fields=["is_active"])
        count = self.notifications(self.other).count()
        lesson.starts_at += timedelta(hours=2)
        lesson.ends_at += timedelta(hours=2)
        lesson.save()
        self.assertEqual(self.notifications(self.other).count(), count)
        self.assertEqual(self.notifications(enrollments[1].student, "lesson_changed").count(), 1)

    def test_group_teacher_assignment_notifies_new_teacher_only_once(self):
        group, enrollments, lesson = self.group()
        group.teacher = self.second_teacher
        group.save(update_fields=["teacher"])
        self.assertEqual(self.notifications(self.second_teacher, "group_assigned").count(), 1)
        group.save()
        self.assertEqual(self.notifications(self.second_teacher, "group_assigned").count(), 1)

    def test_rollback_removes_notifications_and_cursor_changes(self):
        before = Notification.objects.count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.approve()
                raise RuntimeError("Rollback test")
        self.assertEqual(Notification.objects.count(), before)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "pending")
        self.approve()
        self.assertEqual(self.notifications(self.student, "enrollment_status").count(), 1)

    def test_deleted_target_falls_back_to_existing_dashboard(self):
        assignment = self.homework()
        row = self.notifications(self.student, "homework_created").get()
        assignment.delete()
        self.assertEqual(row.destination(), reverse("student_dashboard") + "#my-homework")

    def test_each_user_sees_only_own_notifications_and_count(self):
        self.approve()
        self.client.force_login(self.other)
        response = self.client.get(reverse("notification_list"))
        self.assertEqual(response.context["unread_notification_count"], 0)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertNotContains(response, "Заявку підтверджено")
        self.client.force_login(self.student)
        response = self.client.get(reverse("notification_list"))
        self.assertEqual(response.context["unread_notification_count"], 1)
        self.assertContains(response, "Заявку підтверджено")
        self.assertIn("no-store", response["Cache-Control"])

    def test_mark_read_requires_owner_post_csrf_and_is_idempotent(self):
        self.approve()
        row = self.notifications(self.student).get()
        url = reverse("notification_mark_read", args=[row.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url).status_code, 403)
        client.get(reverse("notification_list"))
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(url, HTTP_X_CSRFTOKEN=token).status_code, 302)
        row.refresh_from_db()
        first_read = row.read_at
        self.assertIsNotNone(first_read)
        self.assertEqual(client.post(url, HTTP_X_CSRFTOKEN=token).status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.read_at, first_read)
        self.assertEqual(client.get(reverse("notification_list")).context["unread_notification_count"], 0)

    def test_guest_and_staff_cannot_use_student_teacher_notifications(self):
        self.assertEqual(self.client.get(reverse("notification_list")).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("notification_list")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("school_admin_dashboard")), 'class="academy-notification-bell"')

    def test_counter_in_teacher_header_and_pagination(self):
        self.approve()
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("teacher_dashboard"))
        self.assertContains(response, "Сповіщення: 1 непрочитаних")
        for index in range(25):
            Notification.objects.create(recipient=self.teacher, event_key=f"pagination-{index}", kind="test",
                title="Тестове сповіщення", text="Лише в тестовій базі", target_type="enrollment", target_id=self.enrollment.pk)
        response = self.client.get(reverse("notification_list"))
        self.assertEqual(len(response.context["page_obj"]), 20)
        response = self.client.get(reverse("notification_list"), {"page": 2})
        self.assertEqual(len(response.context["page_obj"]), 6)

    def test_duplicate_signal_dispatch_does_not_duplicate_event(self):
        self.approve()
        from django.db.models.signals import post_save
        count = Notification.objects.count()
        post_save.send(sender=Enrollment, instance=self.enrollment, created=False, raw=False, using="default", update_fields=None)
        self.assertEqual(Notification.objects.count(), count)

    def test_missing_migration_does_not_break_existing_learning_pages_or_saves(self):
        self.approve()
        count = Notification.objects.count()
        self.client.force_login(self.teacher)
        with patch("notifications.services.connections") as connections:
            connections.__getitem__.return_value.introspection.table_names.return_value = []
            lesson = self.lesson()
            self.assertEqual(Notification.objects.count(), count)
            response = self.client.get(reverse("teacher_dashboard"))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'class="academy-notification-bell"')
            self.assertEqual(self.client.get(reverse("notification_list")).status_code, 503)

    def test_internal_notifications_do_not_send_email(self):
        self.approve()
        assignment = Assignment.objects.create(lesson=self.lesson(), title="Тест", description="Умова")
        Submission.objects.create(assignment=assignment, student=self.student, answer_text="Відповідь", grade=90)
        self.assertEqual(len(mail.outbox), 0)

    def test_first_noop_save_of_historical_record_does_not_backfill_events(self):
        enrollment = Enrollment(student=self.other, course=self.course, teacher=self.teacher, status="approved")
        Enrollment.objects.bulk_create([enrollment])  # Simulate a pre-feature historical row.
        self.assertFalse(EventCursor.objects.filter(key=f"enrollments.enrollment:{enrollment.pk}").exists())
        enrollment.save()
        self.assertEqual(Notification.objects.count(), 0)

    def test_database_unique_constraint_prevents_duplicate_recipient_event(self):
        self.approve()
        row = self.notifications(self.student).get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(recipient=self.student, event_key=row.event_key, kind=row.kind,
                title=row.title, text=row.text, target_type=row.target_type, target_id=row.target_id)
        self.assertEqual(self.notifications(self.student).count(), 1)

    def test_course_text_is_escaped_and_links_cannot_be_external(self):
        self.course.title = '<script>alert("untrusted-title")</script>'
        self.course.save(update_fields=["title"])
        self.approve()
        self.client.force_login(self.student)
        response = self.client.get(reverse("notification_list"))
        self.assertNotContains(response, '<script>alert(')
        self.assertContains(response, "&lt;script&gt;")
        row = self.notifications(self.student).get()
        row.target_type = "https://outside.invalid/"
        self.assertEqual(row.destination(), reverse("student_dashboard") + "#my-courses")

    def test_homework_on_cancelled_lesson_follows_existing_material_access_rules(self):
        self.approve()
        lesson = self.lesson()
        lesson.status = "cancelled"
        lesson.save(update_fields=["status"])
        assignment = Assignment.objects.create(lesson=lesson, title="Самостійна практика", description="Доступна за чинними правилами")
        row = self.notifications(self.student, "homework_created").get()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(row.destination()).status_code, 200)

    def test_real_past_lesson_change_is_not_silenced_but_historical_creation_is_not_announced(self):
        self.approve()
        start = timezone.now() - timedelta(days=2)
        lesson = Lesson.objects.create(enrollment=self.enrollment, teacher=self.teacher,
            starts_at=start, ends_at=start+timedelta(hours=1), status="completed")
        self.assertFalse(self.notifications(self.student, "lesson_created").exists())
        count = Notification.objects.count()
        lesson.save()
        self.assertEqual(Notification.objects.count(), count)
        lesson.status = "cancelled"
        lesson.save(update_fields=["status"])
        self.assertEqual(self.notifications(self.student, "lesson_cancelled").count(), 1)
