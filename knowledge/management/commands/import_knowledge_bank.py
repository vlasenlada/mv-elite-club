from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from knowledge.importing import import_bank, read_bank


class Command(BaseCommand):
    help = "Перевіряє JSON; із --apply імпортує лише нові чернетки, без перезаписування."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            bank = read_bank(options["path"])
            if not options["apply"]:
                self.stdout.write(f"Структуру перевірено: {len(bank['tests'])} тестів. Дані не змінювалися. Для імпорту чернеток потрібен --apply.")
                return
            tests = import_bank(bank)
        except (ValidationError, ValueError, TypeError, KeyError, OSError) as error:
            raise CommandError(f"Імпорт відхилено: {error}") from None
        self.stdout.write(self.style.SUCCESS(f"Створено чернеток: {len(tests)}. Опублікування вимкнене."))
