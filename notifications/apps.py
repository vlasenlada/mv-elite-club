from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "notifications"
    verbose_name = "Сповіщення"

    def ready(self):
        from . import signals  # noqa: F401
