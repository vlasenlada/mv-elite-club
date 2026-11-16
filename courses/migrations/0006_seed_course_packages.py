from django.db import migrations


def seed_packages(apps, schema_editor):
    Course = apps.get_model("courses", "Course")
    Package = apps.get_model("courses", "CoursePackage")
    alias = schema_editor.connection.alias
    for course_id in Course.objects.using(alias).values_list("pk", flat=True):
        for learning_format in ("individual", "group"):
            for count in (4, 8, 12):
                Package.objects.using(alias).get_or_create(course_id=course_id,
                    learning_format=learning_format, lesson_count=count)


class Migration(migrations.Migration):
    dependencies = [("courses", "0005_coursepackage")]
    operations = [migrations.RunPython(seed_packages, migrations.RunPython.noop)]
