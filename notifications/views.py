from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .models import Notification
from .services import eligible, storage_ready


@login_required
@never_cache
def notification_list(request):
    if not eligible(request.user):
        return HttpResponseForbidden("Сповіщення доступні учням і викладачам.")
    if not storage_ready():
        return render(request, "notifications/list.html", {"unavailable": True}, status=503)
    page = Paginator(Notification.objects.filter(recipient=request.user).select_related("recipient"), 20).get_page(request.GET.get("page"))
    for notification in page:
        notification.target_url = notification.destination()
    return render(request, "notifications/list.html", {"page_obj": page})


@login_required
@never_cache
@require_POST
def mark_read(request, pk):
    if not eligible(request.user):
        return HttpResponseForbidden("Немає доступу до сповіщення.")
    if not storage_ready():
        return redirect("notification_list")
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    Notification.objects.filter(pk=notification.pk, recipient=request.user, read_at=None).update(read_at=timezone.now())
    return redirect("notification_list")
