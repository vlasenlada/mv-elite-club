from .models import Notification
from .services import eligible, storage_ready


def notification_badge(request):
    available = eligible(request.user) and storage_ready()
    return {"notifications_available": available,
        "unread_notification_count": Notification.objects.filter(recipient=request.user, read_at=None).count() if available else 0}
