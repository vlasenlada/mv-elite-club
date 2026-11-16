from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course
from enrollments.models import Enrollment
from learning.models import Assignment, Submission
from scheduling.models import Lesson
from users.models import User

from .models import Attendance, GroupMembership, StudyGroup


class GroupCapacityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.get(slug="english-primary")
        cls.teacher = User.objects.create_user(username="capacity_teacher", role="teacher")
        cls.admin = User.objects.create_superuser(username="capacity_admin", email="admin@example.com", password=None, role="admin")
        cls.students = [User.objects.create_user(username=f"member_{n}") for n in range(3)]
        cls.enrollments = [Enrollment.objects.create(
            student=s, course=cls.course, status="approved", requested_format="group", format_is_explicit=True,
        ) for s in cls.students]
        cls.group = StudyGroup.objects.create(name="Навчальна група", course=cls.course, teacher=cls.teacher, capacity=2)
        cls.member = GroupMembership.objects.create(group=cls.group, enrollment=cls.enrollments[0])
        cls.lesson = Lesson.objects.create(
            group=cls.group, teacher=cls.teacher, starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=1),
        )
        cls.assignment = Assignment.objects.create(lesson=cls.lesson, title="Спільна робота", description="Завдання")
        cls.submission = Submission.objects.create(assignment=cls.assignment, student=cls.students[0], answer_text="Відповідь", grade=88)

    def group_data(self, **overrides):
        return {"name": self.group.name, "course": self.course.pk, "teacher": self.teacher.pk,
                "capacity": 2, "is_active": "on", "memberships-TOTAL_FORMS": "0", "memberships-INITIAL_FORMS": "0", "_save": "Save", **overrides}

    def learning_snapshot(self):
        return {m.__name__: list(m.objects.order_by("pk").values()) for m in (Lesson, Assignment, Submission, Attendance)}

    def test_any_positive_capacity_can_be_chosen_without_fixed_group_sizes(self):
        for number in (1, 2, 3, 4, 5, 8, 10, 37):
            with self.subTest(capacity=number):
                group = StudyGroup.objects.create(name=f"Група {number}", course=self.course, teacher=self.teacher, capacity=number)
                self.assertEqual(group.capacity, number)
                self.assertEqual(group.free_places, number)

    def test_invalid_capacity_values_are_validation_errors(self):
        for number in (0, -1, "invalid"):
            with self.subTest(capacity=number):
                group = StudyGroup(name="Невалідна", course=self.course, teacher=self.teacher, capacity=number)
                with self.assertRaises(ValidationError):
                    group.full_clean()

    def test_member_and_free_place_counts_update_after_admission(self):
        self.assertEqual((self.group.member_count, self.group.free_places), (1, 1))
        GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[1])
        self.assertEqual((self.group.member_count, self.group.free_places), (2, 0))
        self.assertEqual(self.lesson.attendances.count(), 2)

    def test_direct_model_save_cannot_overfill_group(self):
        GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[1])
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[2])
        self.assertEqual(self.group.member_count, 2)

    def test_deactivation_frees_a_place_and_preserves_history(self):
        before = self.learning_snapshot()
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        self.assertEqual((self.group.member_count, self.group.free_places), (0, 2))
        self.assertEqual(self.learning_snapshot(), before)

    def test_capacity_cannot_shrink_below_active_member_count(self):
        GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[1])
        self.group.capacity = 1
        with self.assertRaises(ValidationError):
            self.group.save(update_fields=["capacity"])
        self.group.refresh_from_db()
        self.assertEqual(self.group.capacity, 2)

    def test_existing_capacity_cannot_be_cleared_to_bypass_admission_limit(self):
        self.group.capacity = None
        with self.assertRaises(ValidationError):
            self.group.save(update_fields=["capacity"])
        self.group.refresh_from_db()
        self.assertEqual(self.group.capacity, 2)

    def test_capacity_can_change_without_changing_teaching_records(self):
        before = self.learning_snapshot()
        self.group.capacity = 10
        self.group.save(update_fields=["capacity"])
        self.assertEqual(self.learning_snapshot(), before)
        self.assertEqual(self.group.free_places, 9)

    def test_admin_new_group_requires_capacity(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:classrooms_studygroup_add"), self.group_data(name="Без ліміту", capacity=""))
        self.assertEqual(response.status_code, 200)
        self.assertIn("capacity", response.context["adminform"].form.errors)
        self.assertFalse(StudyGroup.objects.filter(name="Без ліміту").exists())

    def test_admin_batch_admission_cannot_overfill_new_group(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:classrooms_studygroup_add"), self.group_data(
            name="Завеликий набір", capacity=1,
            **{"memberships-TOTAL_FORMS": "2", "memberships-0-enrollment": self.enrollments[1].pk,
               "memberships-0-is_active": "on", "memberships-1-enrollment": self.enrollments[2].pk,
               "memberships-1-is_active": "on"},
        ))
        self.assertContains(response, "перевищує місткість")
        self.assertFalse(StudyGroup.objects.filter(name="Завеликий набір").exists())
        self.assertEqual(GroupMembership.objects.count(), 1)

    def test_admin_batch_cannot_duplicate_same_enrollment(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:classrooms_studygroup_add"), self.group_data(
            name="Дублікат", capacity=3,
            **{"memberships-TOTAL_FORMS": "2", "memberships-0-enrollment": self.enrollments[1].pk,
               "memberships-0-is_active": "on", "memberships-1-enrollment": self.enrollments[1].pk,
               "memberships-1-is_active": "on"},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StudyGroup.objects.filter(name="Дублікат").exists())

    def test_admin_individual_admission_cannot_overfill_existing_group(self):
        GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[1])
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:classrooms_groupmembership_add"), {
            "group": self.group.pk, "enrollment": self.enrollments[2].pk, "is_active": "on", "_save": "Save",
        })
        self.assertContains(response, "немає вільних місць")
        self.assertEqual(self.group.member_count, 2)

    def test_admin_cannot_reduce_capacity_below_members(self):
        GroupMembership.objects.create(group=self.group, enrollment=self.enrollments[1])
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:classrooms_studygroup_change", args=[self.group.pk]), self.group_data(capacity=1))
        self.assertEqual(response.status_code, 200)
        self.assertIn("capacity", response.context["adminform"].form.errors)
        self.group.refresh_from_db()
        self.assertEqual(self.group.capacity, 2)

    def test_admin_can_edit_capacity_and_see_counts(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:classrooms_studygroup_change", args=[self.group.pk]), self.group_data(capacity=8))
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual((self.group.capacity, self.group.free_places), (8, 7))
        self.assertEqual(self.client.get(reverse("admin:classrooms_studygroup_add")).status_code, 200)

    def test_legacy_group_without_capacity_is_preserved_but_admin_must_set_limit_for_new_admissions(self):
        group = StudyGroup.objects.create(name="Історична", course=self.course, teacher=self.teacher)
        self.assertIsNone(group.capacity)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:classrooms_groupmembership_add"), {
            "group": group.pk, "enrollment": self.enrollments[1].pk, "is_active": "on", "_save": "Save",
        })
        self.assertContains(response, "вкажіть місткість")
        self.assertEqual(group.member_count, 0)

    def test_student_cannot_change_capacity_through_admin(self):
        self.client.force_login(self.students[0])
        response = self.client.post(reverse("admin:classrooms_studygroup_change", args=[self.group.pk]), self.group_data(capacity=99))
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.capacity, 2)

    def test_student_sees_participant_and_free_place_counts_without_peer_records(self):
        self.client.force_login(self.students[0])
        response = self.client.get(reverse("group_detail", args=[self.group.pk]))
        self.assertContains(response, "Учасників: 1 / 2")
        self.assertContains(response, "Вільних місць: 1")
        self.assertNotContains(response, self.students[1].username)
