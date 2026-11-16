from datetime import UTC, date, datetime, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from classrooms.models import GroupMembership, StudyGroup
from courses.models import Course
from enrollments.models import Enrollment
from learning.models import Assignment
from notifications.models import Notification
from users.models import User

from .models import Lesson
from .views import KYIV, lesson_card, period_for


class CalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user(username="calendar-student")
        cls.other = User.objects.create_user(username="private-classmate")
        cls.teacher = User.objects.create_user(username="calendar-teacher", role="teacher")
        cls.outsider = User.objects.create_user(username="other-teacher", role="teacher")
        cls.course = Course.objects.create(title="Calendar English", subject="english", price=0)
        cls.enrollment = Enrollment.objects.create(student=cls.student, course=cls.course, teacher=cls.teacher, status="approved")
        cls.start = datetime(2030, 5, 15, 14, tzinfo=KYIV)
        cls.lesson = Lesson.objects.create(enrollment=cls.enrollment, teacher=cls.teacher,
            starts_at=cls.start, ends_at=cls.start + timedelta(minutes=50), meeting_url="https://example.invalid/secret-meeting")
        cls.group_course = Course.objects.create(title="Group English", subject="english", price=0)
        cls.group = StudyGroup.objects.create(name="Calendar group", course=cls.group_course, teacher=cls.teacher, capacity=8)
        cls.group_enrollment = Enrollment.objects.create(student=cls.student, course=cls.group_course, status="approved", requested_format="group", format_is_explicit=True)
        cls.member = GroupMembership.objects.create(group=cls.group, enrollment=cls.group_enrollment)
        cls.other_enrollment = Enrollment.objects.create(student=cls.other, course=cls.group_course, status="approved", requested_format="group", format_is_explicit=True)
        GroupMembership.objects.create(group=cls.group, enrollment=cls.other_enrollment)
        cls.group_lesson = Lesson.objects.create(group=cls.group, teacher=cls.teacher,
            starts_at=cls.start + timedelta(days=1), ends_at=cls.start + timedelta(days=1, hours=1))
        cls.assignment = Assignment.objects.create(lesson=cls.lesson, title="Calendar homework", description="Task")

    def setUp(self):
        self.client.force_login(self.student)
        self.url = reverse("lesson_calendar")
        self.detail = reverse("lesson_detail", args=[self.lesson.pk])

    def calendar(self, mode="week", day="2030-05-15"):
        return self.client.get(self.url, {"view": mode, "date": day})

    def ids(self, response):
        return [card["lesson"].pk for card in response.context["cards"]]

    def test_week_contains_individual_and_group_once(self):
        response = self.calendar()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids(response), [self.lesson.pk, self.group_lesson.pk])
        self.assertEqual(len(response.context["days"]), 7)
        self.assertNotContains(response, self.lesson.meeting_url)
        self.assertNotContains(response, self.other.username)

    def test_month_and_list_use_same_period(self):
        for mode in ("month", "list"):
            with self.subTest(mode=mode):
                response = self.calendar(mode)
                self.assertEqual(set(self.ids(response)), {self.lesson.pk, self.group_lesson.pk})
                self.assertEqual(response.context["start"], date(2030, 5, 1))
                self.assertEqual(response.context["last"], date(2030, 5, 31))

    def test_month_grid_aligns_monday_and_covers_leap_day(self):
        response = self.calendar("month", "2032-02-29")
        days = response.context["days"]
        self.assertEqual(days[0]["date"].weekday(), 0)
        self.assertEqual(len(days) % 7, 0)
        self.assertIn(date(2032, 2, 29), [day["date"] for day in days])

    def test_year_navigation(self):
        start, end, previous, following = period_for(date(2030, 12, 31), "month")
        self.assertEqual((start, end, previous, following), (date(2030, 12, 1), date(2031, 1, 1), date(2030, 11, 1), date(2031, 1, 1)))
        self.assertEqual(period_for(date(2031, 1, 1), "week")[:2], (date(2030, 12, 30), date(2031, 1, 6)))

    def test_invalid_input_is_400(self):
        for day in ("bad", "2030-02-30", "0001-01-01", "9999-12-31", ""):
            self.assertEqual(self.calendar(day=day).status_code, 400)
        self.assertEqual(self.calendar(mode="invalid").status_code, 400)

    def test_empty_period(self):
        response = self.calendar(day="2031-09-01")
        self.assertEqual(self.ids(response), [])
        self.assertContains(response, "На цей період занять немає")

    def test_guest_redirected_and_post_forbidden(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.get(self.detail).status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.detail).status_code, 405)

    def test_staff_and_admin_have_no_calendar_access(self):
        for role, staff in (("admin", True), ("student", True), ("admin", False)):
            user = User.objects.create_user(username=f"blocked-{role}-{staff}", role=role, is_staff=staff)
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.get(self.detail).status_code, 403)

    def test_student_cannot_see_others_individual_lesson(self):
        self.client.force_login(self.other)
        self.assertNotIn(self.lesson.pk, self.ids(self.calendar()))
        self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_teacher_only_sees_assigned_lessons(self):
        self.client.force_login(self.teacher)
        self.assertEqual(set(self.ids(self.calendar())), {self.lesson.pk, self.group_lesson.pk})
        self.client.force_login(self.outsider)
        self.assertEqual(self.ids(self.calendar()), [])
        self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_group_details_hide_classmates_from_student(self):
        url = reverse("lesson_detail", args=[self.group_lesson.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.other.username)
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertContains(response, self.other.username)
        self.assertContains(response, reverse("group_detail", args=[self.group.pk]))

    def test_inactive_membership_revokes_calendar_and_detail_access(self):
        self.member.is_active = False
        self.member.save()
        self.assertNotIn(self.group_lesson.pk, self.ids(self.calendar()))
        self.assertEqual(self.client.get(reverse("lesson_detail", args=[self.group_lesson.pk])).status_code, 404)

    def test_cancelled_enrollment_revokes_links(self):
        self.enrollment.status = "cancelled"
        self.enrollment.save()
        self.assertNotIn(self.lesson.pk, self.ids(self.calendar()))
        self.assertEqual(self.client.get(self.detail).status_code, 404)
        self.assertEqual(self.client.get(reverse("join_lesson", args=[self.lesson.pk])).status_code, 404)

    def test_archived_group_revokes_access(self):
        self.group.is_active = False
        self.group.save()
        self.assertNotIn(self.group_lesson.pk, self.ids(self.calendar()))

    def test_student_detail_uses_protected_join_route(self):
        response = self.client.get(self.detail)
        self.assertContains(response, reverse("join_lesson", args=[self.lesson.pk]))
        self.assertNotContains(response, self.lesson.meeting_url)
        self.assertContains(response, reverse("submit_homework", args=[self.assignment.pk]))
        self.assertNotContains(response, reverse("edit_teacher_lesson", args=[self.lesson.pk]))

    def test_cancelled_and_completed_are_not_active(self):
        for status in ("cancelled", "completed"):
            self.lesson.status = status
            self.lesson.save(update_fields=["status"])
            response = self.calendar()
            card = next(card for card in response.context["cards"] if card["lesson"].pk == self.lesson.pk)
            self.assertEqual(card["appearance"], status)
            self.assertNotContains(self.client.get(self.detail), reverse("join_lesson", args=[self.lesson.pk]))

    def test_elapsed_scheduled_lesson_has_past_appearance(self):
        card = lesson_card(self.lesson, self.start + timedelta(hours=2))
        self.assertEqual(card["appearance"], "past")

    @override_settings(TIME_ZONE="UTC")
    def test_kyiv_rendering_ignores_server_or_active_timezone(self):
        with timezone.override("America/New_York"):
            response = self.calendar()
        self.assertContains(response, "14:00")
        self.assertEqual(response.context["cards"][0]["start"].tzinfo, KYIV)
        self.assertEqual(response.context["cards"][0]["duration"], 50)

    def test_dst_week_boundaries_use_local_midnights(self):
        for day in ("2030-03-31", "2030-10-27"):
            response = self.calendar(day=day)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["days"]), 7)
        for month, offset in ((1, 2), (7, 3)):
            start = datetime(2030, month, 10, 0, 15, tzinfo=KYIV)
            lesson = Lesson.objects.create(enrollment=self.enrollment, teacher=self.teacher,
                starts_at=start, ends_at=start + timedelta(minutes=45))
            response = self.calendar("list", start.date().isoformat())
            card = next(card for card in response.context["cards"] if card["lesson"].pk == lesson.pk)
            self.assertEqual(card["start"].hour, 0)
            self.assertEqual(card["start"].utcoffset(), timedelta(hours=offset))

    def test_period_uses_interval_overlap_and_excludes_adjacent(self):
        midnight = datetime(2030, 5, 13, tzinfo=KYIV)
        crossing = Lesson.objects.create(enrollment=self.enrollment, starts_at=midnight - timedelta(minutes=30), ends_at=midnight + timedelta(minutes=30))
        adjacent = Lesson.objects.create(enrollment=self.enrollment, starts_at=midnight - timedelta(hours=2), ends_at=midnight)
        response = self.calendar()
        self.assertIn(crossing.pk, self.ids(response))
        self.assertNotIn(adjacent.pk, self.ids(response))
        self.assertEqual(sum(len(day["cards"]) for day in response.context["days"]), len(response.context["cards"]))

    def test_missing_teacher_renders_safely(self):
        self.lesson.teacher = None
        self.lesson.save(update_fields=["teacher"])
        self.assertContains(self.client.get(self.detail), "Уточнюється")

    def test_existing_http_reschedule_updates_calendar_and_notification(self):
        self.client.force_login(self.teacher)
        new_start = self.start + timedelta(days=10)
        response = self.client.post(reverse("edit_teacher_lesson", args=[self.lesson.pk]), {
            "starts_at": new_start.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (new_start + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M"), "meeting_url": self.lesson.meeting_url})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.lesson.pk, self.ids(self.calendar()))
        self.assertIn(self.lesson.pk, self.ids(self.calendar(day="2030-05-25")))
        row = Notification.objects.filter(recipient=self.student, target_type="lesson", target_id=self.lesson.pk).first()
        self.assertEqual(row.destination(), self.detail)
        self.assertEqual(self.client.get(row.destination()).status_code, 200)

    def test_notification_falls_back_when_access_is_revoked(self):
        row = Notification.objects.filter(recipient=self.student, target_type="lesson", target_id=self.lesson.pk).first()
        self.assertEqual(row.destination(), self.detail)
        self.enrollment.status = "cancelled"
        self.enrollment.save()
        self.assertEqual(row.destination(), self.url)

    def test_teacher_detail_links_existing_actions_and_csrf(self):
        self.client.force_login(self.teacher)
        response = self.client.get(self.detail)
        self.assertContains(response, reverse("edit_teacher_lesson", args=[self.lesson.pk]))
        self.assertContains(response, reverse("update_lesson_status", args=[self.lesson.pk]))
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, self.assignment.title)

    def test_pages_are_private_and_read_only(self):
        before = (Lesson.objects.count(), Notification.objects.count())
        for url in (self.url, self.detail):
            response = self.client.get(url)
            self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(before, (Lesson.objects.count(), Notification.objects.count()))

    def test_rejoining_group_does_not_duplicate_lesson(self):
        self.member.is_active = False
        self.member.save()
        GroupMembership.objects.create(group=self.group, enrollment=self.group_enrollment)
        response = self.calendar()
        self.assertEqual(self.ids(response).count(self.group_lesson.pk), 1)
        self.assertContains(response, 'class="cal-detail-link" href="' + reverse("lesson_detail", args=[self.group_lesson.pk]) + '"', count=1)

    def test_membership_start_prevents_access_to_earlier_lesson(self):
        earlier = self.member.joined_at - timedelta(days=1)
        lesson = Lesson.objects.create(group=self.group, teacher=self.teacher, starts_at=earlier, ends_at=earlier + timedelta(hours=1))
        response = self.calendar("list", earlier.date().isoformat())
        self.assertNotIn(lesson.pk, self.ids(response))
        self.assertEqual(self.client.get(reverse("lesson_detail", args=[lesson.pk])).status_code, 404)

    def test_dst_repeated_hour_keeps_two_distinct_lessons_and_actual_duration(self):
        for hour in (0, 1):
            start = datetime(2030, 10, 27, hour, 30, tzinfo=UTC)
            Lesson.objects.create(enrollment=self.enrollment, teacher=self.teacher, starts_at=start, ends_at=start + timedelta(minutes=20))
        response = self.calendar("list", "2030-10-27")
        cards = response.context["cards"]
        self.assertEqual(len(cards), 2)
        self.assertEqual([card["start"].hour for card in cards], [3, 3])
        self.assertNotEqual(cards[0]["start"].utcoffset(), cards[1]["start"].utcoffset())
        self.assertEqual([card["duration"] for card in cards], [20, 20])

    def test_existing_edit_still_rejects_schedule_conflict(self):
        self.client.force_login(self.teacher)
        start = self.group_lesson.starts_at
        response = self.client.post(reverse("edit_teacher_lesson", args=[self.lesson.pk]), {
            "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (start + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M"), "meeting_url": self.lesson.meeting_url})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.starts_at, self.start)

    def test_cancellation_notification_opens_details_without_join(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("update_lesson_status", args=[self.lesson.pk]), {"status": "cancelled"})
        row = Notification.objects.get(recipient=self.student, target_id=self.lesson.pk, kind="lesson_cancelled")
        self.client.force_login(self.student)
        response = self.client.get(row.destination())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Скасоване")
        self.assertNotContains(response, reverse("join_lesson", args=[self.lesson.pk]))
