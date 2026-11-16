from copy import deepcopy
import unittest

from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from datetime import timedelta


class MigrationCompatibilityTests(unittest.TestCase):
    """Exercise the old schema -> new schema transition in an isolated RAM DB."""

    def test_old_course_and_all_learning_records_survive_migration(self):
        alias = "migration_compatibility"
        config = deepcopy(connections["default"].settings_dict)
        config["NAME"] = ":memory:"
        isolated = DatabaseWrapper(config, alias=alias)
        connections[alias] = isolated
        try:
            executor = MigrationExecutor(isolated)
            old_targets = [
                ("courses", "0001_initial"), ("users", "0001_initial"),
                ("enrollments", "0002_enrollment_teacher"),
                ("scheduling", "0002_lesson_teacher"), ("learning", "0001_initial"),
            ]
            executor.migrate(old_targets)
            old_apps = executor.loader.project_state(old_targets).apps
            User = old_apps.get_model("users", "User")
            student = User.objects.using(alias).create(username="legacy_student", role="student")
            teacher = User.objects.using(alias).create(username="legacy_teacher", role="teacher")
            course = old_apps.get_model("courses", "Course").objects.using(alias).create(
                title="Англійська для початківців", subject="english", description="Existing description",
                lesson_format="individual", price="150.00", is_published=True,
            )
            enrollment = old_apps.get_model("enrollments", "Enrollment").objects.using(alias).create(
                student=student, teacher=teacher, course=course, status="approved",
            )
            lesson = old_apps.get_model("scheduling", "Lesson").objects.using(alias).create(
                enrollment=enrollment, teacher=teacher, starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(hours=1), status="completed",
            )
            assignment = old_apps.get_model("learning", "Assignment").objects.using(alias).create(
                lesson=lesson, title="Existing homework", description="Existing task",
            )
            old_apps.get_model("learning", "Submission").objects.using(alias).create(
                assignment=assignment, student=student, answer_text="Existing answer", grade=88,
                feedback="Existing feedback",
            )
            labels = [("users", "User"), ("courses", "Course"), ("enrollments", "Enrollment"),
                      ("scheduling", "Lesson"), ("learning", "Assignment"), ("learning", "Submission")]
            before = {label: list(old_apps.get_model(*label).objects.using(alias).order_by("pk").values()) for label in labels}
            executor = MigrationExecutor(isolated)
            targets = executor.loader.graph.leaf_nodes()
            executor.migrate(targets)
            new_apps = executor.loader.project_state(targets).apps
            for label, records in before.items():
                for record in records:
                    after = new_apps.get_model(*label).objects.using(alias).filter(pk=record["id"]).values(*record.keys()).get()
                    self.assertEqual(after, record, msg=f"Historical fields changed: {label}")
            Course = new_apps.get_model("courses", "Course")
            self.assertEqual(Course.objects.using(alias).count(), 7)
            legacy = Course.objects.using(alias).get(pk=course.pk)
            self.assertTrue(legacy.supports_group and legacy.supports_individual)
            self.assertEqual(Course.objects.using(alias).filter(title=legacy.title).count(), 1)
            self.assertEqual(Course.objects.using(alias).filter(price__isnull=True).count(), 6)
            self.assertEqual(new_apps.get_model("classrooms", "Attendance").objects.using(alias).count(), 0)
            group = new_apps.get_model("classrooms", "StudyGroup").objects.using(alias).create(
                course_id=legacy.pk, teacher_id=teacher.pk, name="Legacy course group",
            )
            self.assertEqual(group.course_id, course.pk)
        finally:
            isolated.close()
            del connections[alias]
