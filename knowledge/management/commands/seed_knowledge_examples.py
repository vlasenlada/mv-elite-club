from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from courses.models import Course
from knowledge.importing import import_bank, read_bank
from knowledge.models import Assessment, CourseRecommendation


class Command(BaseCommand):
    help = "Авторські ознайомчі приклади: за замовчуванням лише перевірка, із --apply — нові чернетки."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            bank = read_bank(Path(__file__).resolve().parents[2] / "data" / "examples.json")
            if not options["apply"]:
                self.stdout.write("7 чернеток / 15 авторських завдань. База не змінена. --apply потрібен лише після дозволеного застосування міграції.")
                return
            bank["tests"] = [t for t in bank["tests"] if not Assessment.objects.filter(subject=t["subject"], slug=t["slug"]).exists()]
            tests = import_bank(bank)
            for test in tests:
                candidates = list(Course.objects.filter(is_published=True, subject=test.subject, grade_band=test.effective_band))
                if len(candidates) == 1:
                    CourseRecommendation.objects.create(assessment=test, course=candidates[0], explanation="Предмет і вікова група відповідають обраному тесту; цілі уточнимо на пробному.")
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise CommandError(f"Приклади не імпортовано: {error}") from None
        self.stdout.write(self.style.SUCCESS(f"Створено нових чернеток: {len(tests)}. Жоден тест не опубліковано; наявні матеріали не перезаписані."))
