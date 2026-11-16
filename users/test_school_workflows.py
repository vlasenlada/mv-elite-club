from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from classrooms.models import Attendance, GroupMembership, StudyGroup
from courses.models import Course
from enrollments.models import Enrollment
from learning.models import Assignment, Submission
from scheduling.models import Lesson
from users.models import User


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SchoolWorkflowTests(TestCase):
    """Exercise real HTTP forms and CSRF using disposable test records only."""

    password = "Workflow-Only-K9!46"

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.get(slug="english-primary")
        cls.course.individual_price = Decimal("101.25")
        cls.course.group_price = Decimal("53.75")
        cls.course.save(update_fields=["individual_price", "group_price"])
        cls.legacy = Course.objects.create(
            title="Англійська для початківців", subject="english",
            description="Історичний тестовий курс", lesson_format="individual",
            is_published=True,
        )
        cls.teacher = User.objects.create_user(username="flow_teacher", password=cls.password, role="teacher")
        cls.other_teacher = User.objects.create_user(username="flow_other_teacher", password=cls.password, role="teacher")
        cls.admin = User.objects.create_superuser(username="flow_admin", password=cls.password, email="admin@example.invalid", role="admin")
        cls.outsider = User.objects.create_user(username="flow_outsider", password=cls.password)

    def actor(self, user):
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        return client

    def post(self, client, name, data, *args, follow=False):
        # The actual home page contains a CSRF-protected login/logout form.
        client.get(reverse("login") if "sessionid" not in client.cookies else reverse("home"))
        token = client.cookies["csrftoken"].value
        return client.post(reverse(name, args=args), data, HTTP_X_CSRFTOKEN=token, follow=follow)

    def register_student(self, username):
        client = Client(enforce_csrf_checks=True)
        response = self.post(client, "register", {
            "username": username, "email": username + "@example.invalid",
            "password1": self.password, "password2": self.password,
            "role": "admin", "is_staff": "on", "is_superuser": "on",
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username=username)
        self.assertEqual(user.role, "student")
        self.assertFalse(user.is_staff or user.is_superuser)
        return client, user

    def apply_and_approve(self, client, student, lesson_format):
        response = self.post(client, "enroll_course", {"requested_format": lesson_format}, self.course.pk)
        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.get(student=student, course=self.course)
        self.assertEqual((enrollment.status, enrollment.requested_format), ("pending", lesson_format))
        self.assertTrue(enrollment.format_is_explicit)
        self.assertContains(client.get(reverse("student_dashboard")), enrollment.get_requested_format_display())
        admin = self.actor(self.admin)
        self.assertContains(admin.get(reverse("school_admin_dashboard")), student.username)
        self.assertEqual(self.post(admin, "update_enrollment_status", {"status": "approved"}, enrollment.pk).status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "approved")
        return enrollment

    def times(self, day=2):
        start = (timezone.now() + timedelta(days=day)).replace(second=0, microsecond=0)
        return {"starts_at": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
                "ends_at": timezone.localtime(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")}

    def individual_fixture(self):
        enrollment = Enrollment.objects.create(student=self.outsider, course=self.course,
            teacher=self.teacher, status="approved", format_is_explicit=True)
        start = timezone.now() + timedelta(days=2)
        lesson = Lesson.objects.create(enrollment=enrollment, teacher=self.teacher,
            starts_at=start, ends_at=start + timedelta(hours=1))
        assignment = Assignment.objects.create(lesson=lesson, title="Приватна робота", description="Умова")
        submission = Submission.objects.create(assignment=assignment, student=self.outsider, answer_text="Приватна відповідь", grade=72)
        return enrollment, lesson, assignment, submission

    def test_default_login_redirects_each_role_to_its_own_dashboard(self):
        for user, destination in [(self.outsider, "student_dashboard"), (self.teacher, "teacher_dashboard"), (self.admin, "school_admin_dashboard")]:
            with self.subTest(role=user.role):
                client = Client(enforce_csrf_checks=True)
                response = self.post(client, "login", {"username": user.username, "password": self.password})
                self.assertRedirects(response, reverse(destination))

    def test_registration_without_return_url_opens_student_dashboard(self):
        client, student = self.register_student("flow_new_default")
        self.assertEqual(client.session["_auth_user_id"], str(student.pk))
        client.logout()
        response = self.post(client, "register", {"username": "flow_default_redirect", "email": "default@example.invalid",
            "password1": self.password, "password2": self.password})
        self.assertRedirects(response, reverse("student_dashboard"))

    def test_login_preserves_safe_next_but_rejects_external_destination(self):
        for target, expected in [(self.course.get_absolute_url() + "?format=group", self.course.get_absolute_url() + "?format=group"),
                                 ("https://outside.invalid/", reverse("teacher_dashboard"))]:
            with self.subTest(target=target):
                client = Client(enforce_csrf_checks=True)
                response = self.post(client, "login", {"username": self.teacher.username, "password": self.password, "next": target})
                self.assertRedirects(response, expected)

    def test_wrong_password_logout_and_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        response = self.post(client, "login", {"username": self.teacher.username, "password": "incorrect"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", client.session)
        student_client, student = self.register_student("flow_csrf")
        self.assertEqual(student_client.post(reverse("enroll_course", args=[self.course.pk]), {"requested_format": "group"}).status_code, 403)
        self.assertFalse(Enrollment.objects.filter(student=student).exists())
        self.assertEqual(student_client.get(reverse("logout")).status_code, 405)
        self.assertRedirects(self.post(student_client, "logout", {}), reverse("home"))
        self.assertNotIn("_auth_user_id", student_client.session)

    def test_seven_courses_catalog_filters_and_separate_tariffs(self):
        self.assertEqual(Course.objects.count(), 7)
        for course in Course.objects.all():
            response = self.client.get(course.get_absolute_url())
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'class="academy-format-card', count=2)
        for subject in ("english", "math"):
            response = self.client.get(reverse("course_list"), {"subject": subject})
            self.assertTrue(all(c.subject == subject for c in response.context["courses"]))
        for band in ("primary", "middle", "nmt"):
            response = self.client.get(reverse("course_list"), {"grade": band})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["courses"]), 2)
            self.assertTrue(all(c.grade_band == band for c in response.context["courses"]))
        options = self.client.get(self.course.get_absolute_url()).context["learning_options"]
        self.assertEqual({o["value"]: o["price"] for o in options}, {"individual": Decimal("101.25"), "group": Decimal("53.75")})

    def test_individual_registration_to_graded_homework_with_csrf(self):
        student_client, student = self.register_student("flow_individual")
        enrollment = self.apply_and_approve(student_client, student, "individual")
        self.post(student_client, "enroll_course", {"requested_format": "group"}, self.course.pk)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.requested_format, "individual")
        self.assertEqual(Enrollment.objects.filter(student=student, course=self.course).count(), 1)
        self.assertEqual(self.post(self.actor(self.admin), "assign_enrollment_teacher", {"teacher_id": self.teacher.pk}, enrollment.pk).status_code, 302)
        teacher_client = self.actor(self.teacher)
        response = self.post(teacher_client, "teacher_dashboard", {"form_type": "lesson", "enrollment": enrollment.pk, **self.times()})
        self.assertEqual(response.status_code, 302)
        lesson = Lesson.objects.get(enrollment=enrollment)
        self.assertIn(lesson, student_client.get(reverse("student_dashboard")).context["lessons"])
        self.assertIn(lesson, teacher_client.get(reverse("teacher_dashboard")).context["upcoming_lessons"])
        self.assertEqual(self.post(teacher_client, "update_meeting_link", {"meeting_url": "https://meet.example.invalid/individual"}, lesson.pk).status_code, 302)
        self.assertRedirects(student_client.get(reverse("join_lesson", args=[lesson.pk])), "https://meet.example.invalid/individual", fetch_redirect_response=False)
        conflict = self.post(teacher_client, "teacher_dashboard", {"form_type": "lesson", "enrollment": enrollment.pk, **self.times()})
        self.assertEqual(conflict.status_code, 200)
        self.assertTrue(conflict.context["lesson_form"].errors)
        self.assertEqual(Lesson.objects.count(), 1)
        self.assertEqual(self.post(teacher_client, "teacher_dashboard", {"lesson": lesson.pk, "title": "Індивідуальна практика", "description": "Виконай вправу", "due_at": ""}).status_code, 302)
        assignment = Assignment.objects.get(lesson=lesson)
        self.assertContains(student_client.get(reverse("student_dashboard")), assignment.title)
        self.assertEqual(self.post(student_client, "submit_homework", {"answer_text": "Моя індивідуальна відповідь"}, assignment.pk).status_code, 302)
        submission = Submission.objects.get(student=student, assignment=assignment)
        self.assertEqual(self.post(teacher_client, "update_lesson_status", {"status": "completed"}, lesson.pk).status_code, 302)
        self.assertEqual(student_client.get(reverse("join_lesson", args=[lesson.pk])).status_code, 404)
        self.assertEqual(self.post(teacher_client, "review_submission", {"grade": "94", "feedback": "Відмінна індивідуальна робота"}, submission.pk).status_code, 302)
        result = student_client.get(reverse("student_dashboard"))
        self.assertContains(result, "Відмінна індивідуальна робота")
        self.assertContains(result, 'id="my-progress"')
        self.post(student_client, "submit_homework", {"answer_text": "Спроба змінити перевірену роботу"}, assignment.pk)
        submission.refresh_from_db()
        self.assertEqual((submission.grade, submission.answer_text), (94, "Моя індивідуальна відповідь"))

    def test_group_registration_admin_admission_attendance_and_personal_grades(self):
        c1, s1 = self.register_student("flow_group_one")
        c2, s2 = self.register_student("flow_group_two")
        enrollments = [self.apply_and_approve(c, s, "group") for c, s in [(c1, s1), (c2, s2)]]
        self.assertFalse(StudyGroup.objects.exists())
        data = {"name": "Аудит: спільне навчання", "course": self.course.pk, "teacher": self.teacher.pk,
            "capacity": 3, "is_active": "on", "memberships-TOTAL_FORMS": "2", "memberships-INITIAL_FORMS": "0",
            "memberships-MIN_NUM_FORMS": "0", "memberships-MAX_NUM_FORMS": "1000", "_save": "Save"}
        for index, enrollment in enumerate(enrollments):
            data[f"memberships-{index}-enrollment"] = enrollment.pk
            data[f"memberships-{index}-is_active"] = "on"
        response = self.post(self.actor(self.admin), "admin:classrooms_studygroup_add", data)
        self.assertEqual(response.status_code, 302)
        group = StudyGroup.objects.get()
        self.assertEqual((group.member_count, group.free_places), (2, 1))
        teacher_client = self.actor(self.teacher)
        self.assertEqual(self.post(teacher_client, "group_detail", {"group": group.pk, **self.times(), "meeting_url": "https://meet.example.invalid/group"}, group.pk).status_code, 302)
        lesson = Lesson.objects.get(group=group)
        self.assertEqual((Lesson.objects.count(), lesson.attendances.count()), (1, 2))
        for client in (c1, c2):
            self.assertIn(lesson, client.get(reverse("student_dashboard")).context["lessons"])
            self.assertRedirects(client.get(reverse("join_lesson", args=[lesson.pk])), "https://meet.example.invalid/group", fetch_redirect_response=False)
        for student, status in [(s1, "present"), (s2, "late")]:
            attendance = Attendance.objects.get(lesson=lesson, membership__enrollment__student=student)
            self.assertEqual(self.post(teacher_client, "update_attendance", {"status": status, "note": "Особиста відмітка"}, attendance.pk).status_code, 302)
            attendance.refresh_from_db()
            self.assertEqual(attendance.status, status)
        self.post(teacher_client, "teacher_dashboard", {"lesson": lesson.pk, "title": "Групова практика", "description": "Спільна умова", "due_at": ""})
        assignment = Assignment.objects.get(lesson=lesson)
        for client, student, answer, grade in [(c1, s1, "Особиста відповідь один", 91), (c2, s2, "Особиста відповідь два", 76)]:
            self.assertEqual(self.post(client, "submit_homework", {"answer_text": answer}, assignment.pk).status_code, 302)
            submission = Submission.objects.get(student=student, assignment=assignment)
            self.post(teacher_client, "review_submission", {"grade": grade, "feedback": f"Особистий коментар {grade}"}, submission.pk)
            submission.refresh_from_db()
            self.assertEqual((submission.grade, submission.answer_text), (grade, answer))
        self.assertEqual(Submission.objects.filter(assignment=assignment).count(), 2)
        self.assertContains(c1.get(reverse("group_detail", args=[group.pk])), "Особиста відповідь один")
        self.assertNotContains(c1.get(reverse("group_detail", args=[group.pk])), "Особиста відповідь два")
        self.assertNotContains(c2.get(reverse("student_dashboard")), "Особистий коментар 91")
        outsider = self.actor(self.outsider)
        for name, pk in [("group_detail", group.pk), ("join_lesson", lesson.pk), ("submit_homework", assignment.pk)]:
            self.assertEqual(outsider.get(reverse(name, args=[pk])).status_code, 404)
        member = GroupMembership.objects.get(enrollment=enrollments[0])
        self.post(self.actor(self.admin), "admin:classrooms_groupmembership_change", {"group": group.pk, "enrollment": enrollments[0].pk, "_save": "Save"}, member.pk)
        self.assertEqual((group.member_count, group.free_places), (1, 2))
        self.assertEqual(c1.get(reverse("join_lesson", args=[lesson.pk])).status_code, 404)
        self.assertEqual(Submission.objects.filter(assignment=assignment).count(), 2)
        self.assertEqual(Attendance.objects.filter(lesson=lesson).count(), 2)

    def test_admin_cannot_reassign_individual_history_to_another_student_or_course(self):
        enrollment, lesson, assignment, submission = self.individual_fixture()
        other_student = User.objects.create_user(username="flow_history_target")
        other_course = Course.objects.get(slug="math-primary")
        for changed in [{"student": other_student.pk}, {"course": other_course.pk}]:
            with self.subTest(changed=changed):
                response = self.post(self.actor(self.admin), "admin:enrollments_enrollment_change", {
                    "student": enrollment.student_id, "course": self.course.pk, "teacher": self.teacher.pk,
                    "status": "approved", "requested_format": "individual", "_save": "Save", **changed,
                }, enrollment.pk)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.errors)
                enrollment.refresh_from_db()
                self.assertEqual((enrollment.student_id, enrollment.course_id), (self.outsider.pk, self.course.pk))
                submission.refresh_from_db()
                self.assertEqual(submission.grade, 72)

    def test_invalid_review_gives_visible_error_and_preserves_grade(self):
        enrollment, lesson, assignment, submission = self.individual_fixture()
        for grade in ("101", "-1", "invalid", ""):
            with self.subTest(grade=grade):
                response = self.post(self.actor(self.teacher), "review_submission", {"grade": grade, "feedback": "Не зберігати"}, submission.pk, follow=True)
                self.assertContains(response, "Не вдалося зберегти оцінку")
                submission.refresh_from_db()
                self.assertEqual((submission.grade, submission.feedback), (72, ""))

    def test_admin_cannot_switch_to_group_format_while_retaining_individual_teacher(self):
        enrollment = Enrollment.objects.create(student=self.outsider, course=self.course,
            teacher=self.teacher, status="approved", requested_format="individual", format_is_explicit=True)
        response = self.post(self.actor(self.admin), "admin:enrollments_enrollment_change", {
            "student": self.outsider.pk, "course": self.course.pk, "teacher": self.teacher.pk,
            "status": "approved", "requested_format": "group", "_save": "Save",
        }, enrollment.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors)
        enrollment.refresh_from_db()
        self.assertEqual((enrollment.requested_format, enrollment.teacher_id), ("individual", self.teacher.pk))

    def test_roles_cannot_read_or_mutate_foreign_work(self):
        enrollment, lesson, assignment, submission = self.individual_fixture()
        self.assertEqual(self.actor(self.outsider).get(reverse("school_admin_dashboard")).status_code, 403)
        self.assertNotContains(self.actor(self.teacher).get(reverse("student_dashboard"), follow=True), "Приватна відповідь")
        self.assertNotContains(self.actor(self.outsider).get(reverse("teacher_dashboard"), follow=True), "Приватна відповідь")
        self.assertEqual(self.post(self.actor(self.other_teacher), "review_submission", {"grade": 100}, submission.pk).status_code, 404)
        self.assertEqual(self.post(self.actor(self.outsider), "review_submission", {"grade": 100}, submission.pk).status_code, 403)
        self.assertEqual(self.post(self.actor(self.teacher), "update_enrollment_status", {"status": "approved"}, enrollment.pk).status_code, 403)
        submission.refresh_from_db()
        self.assertEqual(submission.grade, 72)

    def departed_member_work(self, grade=None):
        enrollment = Enrollment.objects.create(student=self.outsider, course=self.course,
            requested_format="group", format_is_explicit=True, status="approved")
        group = StudyGroup.objects.create(name="Історія складу", course=self.course, teacher=self.teacher, capacity=8)
        member = GroupMembership.objects.create(group=group, enrollment=enrollment,
            joined_at=timezone.now() - timedelta(days=10))
        start = timezone.now() + timedelta(days=2)
        lesson = Lesson.objects.create(group=group, teacher=self.teacher, starts_at=start, ends_at=start + timedelta(hours=1))
        assignment = Assignment.objects.create(lesson=lesson, title="Робота перед зміною складу", description="Умова")
        submission = Submission.objects.create(assignment=assignment, student=self.outsider,
            answer_text="Виконано до виходу з групи", grade=grade, feedback="Збережена історія" if grade is not None else "")
        member.is_active = False
        member.save(update_fields=["is_active"])
        return group, lesson, submission

    def test_departed_member_keeps_own_grade_for_previously_submitted_future_lesson_work(self):
        group, lesson, submission = self.departed_member_work(grade=84)
        client = self.actor(self.outsider)
        response = client.get(reverse("student_dashboard"))
        self.assertIn(submission, response.context["graded_submissions"])
        response = client.get(reverse("group_detail", args=[group.pk]))
        self.assertIn(submission, response.context["submissions"])
        self.assertEqual(client.get(reverse("join_lesson", args=[lesson.pk])).status_code, 404)
        self.assertEqual(self.post(client, "submit_homework", {"answer_text": "Заборонена нова відповідь"}, submission.assignment_id).status_code, 404)

    def test_teacher_can_finish_reviewing_work_received_before_member_departure(self):
        group, lesson, submission = self.departed_member_work()
        client = self.actor(self.teacher)
        self.assertIn(submission, client.get(reverse("teacher_dashboard")).context["teacher_submissions"])
        response = self.post(client, "review_submission", {"grade": 87, "feedback": "Перевірено після виходу"}, submission.pk)
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.grade, 87)

    def test_unrelated_answer_without_membership_is_not_exposed_to_teacher(self):
        group, lesson, submission = self.departed_member_work()
        intruder = User.objects.create_user(username="flow_invalid_answer")
        invalid = Submission.objects.create(assignment=submission.assignment, student=intruder, answer_text="Недійсний запис")
        client = self.actor(self.teacher)
        self.assertNotIn(invalid, client.get(reverse("teacher_dashboard")).context["teacher_submissions"])
        self.assertEqual(self.post(client, "review_submission", {"grade": 100}, invalid.pk).status_code, 404)
