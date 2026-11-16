from copy import deepcopy
from datetime import timedelta
import unittest

from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


class TariffCompatibilityTests(unittest.TestCase):
    def test_existing_tariffs_formats_group_history_and_palette_survive(self):
        alias = "tariff_compatibility"
        config = deepcopy(connections["default"].settings_dict)
        config["NAME"] = ":memory:"
        isolated = DatabaseWrapper(config, alias=alias)
        connections[alias] = isolated
        try:
            executor = MigrationExecutor(isolated)
            before_targets = [
                ("courses", "0003_seed_school_catalog"), ("enrollments", "0003_enrollment_requested_format"),
                ("classrooms", "0001_initial"), ("scheduling", "0003_lesson_group_alter_lesson_enrollment_and_more"),
                ("learning", "0001_initial"),
            ]
            executor.migrate(before_targets)
            old = executor.loader.project_state(before_targets).apps
            User = old.get_model("users", "User")
            teacher = User.objects.using(alias).create(username="legacy_teacher", role="teacher")
            students = [User.objects.using(alias).create(username=f"legacy_{n}", role="student") for n in range(2)]
            Course = old.get_model("courses", "Course")
            course = Course.objects.using(alias).create(
                title="Англійська для початківців", subject="english", description="Історичний опис",
                lesson_format="individual", price="91.11", is_published=True,
            )
            enrollments = [old.get_model("enrollments", "Enrollment").objects.using(alias).create(
                student=s, course=course, status="approved", requested_format="individual",
            ) for s in students]
            group = old.get_model("classrooms", "StudyGroup").objects.using(alias).create(
                name="Історична група", course=course, teacher=teacher,
            )
            members = [old.get_model("classrooms", "GroupMembership").objects.using(alias).create(
                group=group, enrollment=e, joined_at=timezone.now() - timedelta(days=5),
            ) for e in enrollments]
            lesson = old.get_model("scheduling", "Lesson").objects.using(alias).create(
                group=group, teacher=teacher, starts_at=timezone.now() - timedelta(days=2),
                ends_at=timezone.now() - timedelta(days=2) + timedelta(hours=1), status="completed",
            )
            for member in members:
                old.get_model("classrooms", "Attendance").objects.using(alias).create(lesson=lesson, membership=member, status="present")
            assignment = old.get_model("learning", "Assignment").objects.using(alias).create(lesson=lesson, title="Історичне завдання", description="Умова")
            old.get_model("learning", "Submission").objects.using(alias).create(assignment=assignment, student=students[0], answer_text="Історична відповідь", grade=89)
            labels = [("courses", "Course"), ("enrollments", "Enrollment"), ("classrooms", "StudyGroup"),
                      ("classrooms", "GroupMembership"), ("classrooms", "Attendance"), ("scheduling", "Lesson"),
                      ("learning", "Assignment"), ("learning", "Submission"), ("users", "User")]
            snapshots = {label: list(old.get_model(*label).objects.using(alias).order_by("pk").values()) for label in labels}
            order = list(Course.objects.using(alias).filter(is_published=True).order_by("-created_at").values_list("pk", flat=True))
            executor = MigrationExecutor(isolated)
            targets = executor.loader.graph.leaf_nodes()
            executor.migrate(targets)
            new = executor.loader.project_state(targets).apps
            for label, rows in snapshots.items():
                for row in rows:
                    actual = new.get_model(*label).objects.using(alias).filter(pk=row["id"]).values(*row.keys()).get()
                    self.assertEqual(actual, row, msg=f"Historical values changed: {label}")
            Course = new.get_model("courses", "Course")
            self.assertEqual(Course.objects.using(alias).count(), 7)
            self.assertEqual(Course.objects.using(alias).filter(individual_price__isnull=True, group_price__isnull=True).count(), 7)
            self.assertEqual(new.get_model("enrollments", "Enrollment").objects.using(alias).filter(format_is_explicit=False).count(), 2)
            self.assertIsNone(new.get_model("classrooms", "StudyGroup").objects.using(alias).get(pk=group.pk).capacity)
            self.assertEqual(new.get_model("courses", "CoursePackage").objects.using(alias).filter(price__isnull=True).count(), 42)
            self.assertEqual(new.get_model("enrollments", "PackageAccount").objects.using(alias).count(), 0)
            self.assertEqual(new.get_model("enrollments", "PackageUsage").objects.using(alias).count(), 0)
            self.assertFalse(new.get_model("users", "User").objects.using(alias).exclude(verified_email=None).exists())
            for index, course_id in enumerate(order):
                expected = "blue" if index == 1 else "wheat" if index == 2 else "cream"
                self.assertEqual(Course.objects.using(alias).get(pk=course_id).card_tone, expected)
        finally:
            isolated.close()
            del connections[alias]
