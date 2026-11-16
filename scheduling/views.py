"""Read-only calendar; teaching changes use the existing handlers."""
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .selectors import calendar_lessons

KYIV = ZoneInfo("Europe/Kyiv")


def period_for(day, mode):
    if mode == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7), start - timedelta(days=7), start + timedelta(days=7)
    start = day.replace(day=1)
    end = start + timedelta(days=monthrange(day.year, day.month)[1])
    return start, end, (start - timedelta(days=1)).replace(day=1), end


def lesson_card(lesson, now):
    return {
        "lesson": lesson,
        "start": timezone.localtime(lesson.starts_at, KYIV),
        "end": timezone.localtime(lesson.ends_at, KYIV),
        "appearance": (lesson.status if lesson.status != "scheduled" else
                       "past" if lesson.ends_at <= now else "upcoming"),
        "duration": int((lesson.ends_at.astimezone(UTC) - lesson.starts_at.astimezone(UTC)).total_seconds() // 60),
    }


def allowed_role(user):
    return user.is_active and not user.is_staff and user.role in {"student", "teacher"}


@login_required
@never_cache
@require_safe
def calendar_view(request):
    if not allowed_role(request.user):
        return HttpResponseForbidden("Розклад доступний учням і викладачам.")
    with timezone.override(KYIV):
        today = timezone.localdate()
        mode = request.GET.get("view", "week")
        if mode not in {"week", "month", "list"}:
            return HttpResponseBadRequest("Оберіть тиждень, місяць або список.")
        try:
            day = date.fromisoformat(request.GET.get("date", today.isoformat()))
            if not 1900 <= day.year <= 9998:
                raise ValueError
            start, end, previous, following = period_for(day, mode)
        except (ValueError, OverflowError):
            return HttpResponseBadRequest("Некоректна дата. Використайте формат РРРР-ММ-ДД.")
        lower = datetime.combine(start, time.min, KYIV)
        upper = datetime.combine(end, time.min, KYIV)
        now = timezone.now()
        cards = [lesson_card(lesson, now) for lesson in calendar_lessons(request.user).filter(
            starts_at__lt=upper, ends_at__gt=lower).order_by("starts_at", "pk")]
        by_day = {}
        for card in cards:
            by_day.setdefault(max(card["start"].date(), start), []).append(card)
        grid_start = start - timedelta(days=start.weekday()) if mode == "month" else start
        grid_end = end + timedelta(days=(-end.weekday()) % 7) if mode == "month" else end
        days = []
        cursor = grid_start
        while cursor < grid_end:
            days.append({"date": cursor, "today": cursor == today, "outside": not start <= cursor < end,
                         "cards": by_day.get(cursor, [])})
            cursor += timedelta(days=1)
        return render(request, "scheduling/calendar.html", {
            "mode": mode, "selected": day, "start": start, "last": end - timedelta(days=1),
            "previous": previous, "following": following, "today": today,
            "days": days, "cards": cards, "is_teacher": request.user.role == "teacher",
            "weekdays": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"],
        })


@login_required
@never_cache
@require_safe
def lesson_detail(request, pk):
    if not allowed_role(request.user):
        return HttpResponseForbidden("У вас немає доступу до заняття.")
    with timezone.override(KYIV):
        lesson = get_object_or_404(calendar_lessons(request.user), pk=pk)
        teacher = request.user.role == "teacher"
        return render(request, "scheduling/detail.html", {
            "lesson": lesson, "card": lesson_card(lesson, timezone.now()), "is_teacher": teacher,
            "participants": lesson.memberships_at_start().select_related("enrollment__student") if teacher and lesson.group_id else [],
            "assignments": lesson.assignments.all().order_by("pk"),
            "can_join": (not teacher and lesson.status == "scheduled" and bool(lesson.meeting_url)
                         and lesson.has_student(request.user, current=True)),
        })
