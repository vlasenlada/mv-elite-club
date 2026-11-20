from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    name = "knowledge"
    verbose_name = "Тестування"

    def ready(self):
        from . import signals  # noqa: F401
