import hashlib
from datetime import date, datetime

from django.db import connections, transaction
from django.utils import timezone

from users.models import User
from .models import EventCursor, Notification


def eligible(user):
    return user.is_authenticated and user.is_active and not user.is_staff and user.role in {"student", "teacher"}


def storage_ready(using="default"):
    # The pending migration must not break existing teaching workflows.
    tables = set(connections[using].introspection.table_names())
    return {Notification._meta.db_table, EventCursor._meta.db_table} <= tables


def canonical(row):
    if row is None:
        return {}
    result = {}
    for key, value in row.items():
        if key in {"meeting_url", "answer_text", "feedback", "description"}:
            result[key] = hashlib.sha256((value or "").encode()).hexdigest()
        elif isinstance(value, (datetime, date)):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def deliver(ids, *, key, kind, title, text, target_type, target_id, using):
    recipients = User.objects.using(using).filter(pk__in=set(ids), is_active=True, is_staff=False, role__in=["student", "teacher"])
    for recipient in recipients:
        Notification.objects.using(using).get_or_create(recipient=recipient, event_key=key, defaults={
            "kind": kind, "title": title, "text": text, "target_type": target_type, "target_id": target_id,
        })


def students(lesson):
    if lesson.enrollment_id:
        return [lesson.enrollment.student_id] if lesson.enrollment.status == "approved" else []
    if not lesson.group_id or not lesson.group.is_active:
        return []
    return list(lesson.memberships_at_start().filter(is_active=True, enrollment__status="approved").values_list("enrollment__student_id", flat=True).distinct())


def lesson_when(lesson):
    return timezone.localtime(lesson.starts_at).strftime("%d.%m.%Y о %H:%M")


def emit_events(obj, before, after, key, using):
    def send(ids, kind, title, text, target_type=None, target_id=None, suffix=""):
        deliver([pk for pk in ids if pk], key=f"{key}:{kind}{suffix}", kind=kind, title=title, text=text,
            target_type=target_type or obj._meta.model_name, target_id=target_id or obj.pk, using=using)

    label = obj._meta.label_lower
    if label == "enrollments.trialrequest":
        if before.get("status") != obj.status and obj.status in {"approved", "rejected", "cancelled", "completed"}:
            send([obj.user_id], "trial_status", "Статус пробної заявки оновлено", "Перегляньте стан вашого звернення щодо безкоштовного пробного заняття.", "trial")
        if obj.teacher_id and before.get("teacher_id") != obj.teacher_id:
            send([obj.teacher_id], "trial_assigned", "Вам призначено пробну заявку", "Адміністрація призначила вам пробну заявку. Умови та час узгоджуються окремо; це ще не заняття в календарі.", "trial")
    elif label == "enrollments.enrollment":
        if before.get("status") != obj.status and obj.status in {"approved", "rejected", "cancelled"}:
            titles = {"approved": "Заявку підтверджено", "rejected": "Заявку відхилено", "cancelled": "Заявку скасовано"}
            send([obj.student_id], "enrollment_status", titles[obj.status], f"Курс «{obj.course.title}». Статус і формат доступні у вашому кабінеті.")
        if obj.status == "approved" and obj.teacher_id and (before.get("teacher_id") != obj.teacher_id or before.get("status") != "approved"):
            send([obj.teacher_id], "student_assigned", "Вам призначено учня", f"Курс «{obj.course.title}». Перегляньте призначення у своєму кабінеті.")
    elif label == "scheduling.lesson":
        if not before and (obj.status != "scheduled" or not obj.is_active_for_learning or obj.ends_at <= timezone.now()):
            return
        changed = bool(before)
        cancelled = obj.status == "cancelled"
        title = "Заняття скасовано" if cancelled else "Заняття змінено" if changed else "Призначено нове заняття"
        kind = "lesson_cancelled" if cancelled else "lesson_changed" if changed else "lesson_created"
        send(students(obj), kind, title, f"{obj.course.title} · {lesson_when(obj)}. Актуальні дані — у розкладі.")
        if obj.teacher_id:
            new_teacher = before.get("teacher_id") != obj.teacher_id
            send([obj.teacher_id], "lesson_assigned" if new_teacher else kind,
                "Вам призначено заняття" if new_teacher and not cancelled else title,
                f"{obj.course.title} · {lesson_when(obj)}. Перевірте свій розклад.")
        old_teacher = before.get("teacher_id")
        if old_teacher and old_teacher != obj.teacher_id:
            send([old_teacher], "lesson_reassigned", "Призначення заняття змінено", "Призначення вашого заняття оновлено. Перегляньте актуальний розклад у кабінеті.")
    elif label == "learning.assignment":
        if not before or before.get("lesson_id") != obj.lesson_id:
            if obj.lesson.is_active_for_learning:
                send(students(obj.lesson), "homework_created", "Нове домашнє завдання", f"Курс «{obj.lesson.course.title}». Завдання доступне у вашому кабінеті.")
    elif label == "learning.submission":
        if not obj.has_participant_history() or obj.student.role != "student" or obj.student.is_staff:
            return
        if not before or before.get("answer_text") != after["answer_text"]:
            send([obj.assignment.lesson.teacher_id], "answer_received", "Отримано відповідь на завдання" if not before else "Відповідь на завдання оновлено",
                f"Курс «{obj.assignment.lesson.course.title}». Перегляньте роботу у своєму кабінеті.")
        if obj.grade is not None and (before.get("grade") != obj.grade or before.get("feedback") != after["feedback"]):
            send([obj.student_id], "work_reviewed", "Домашню роботу перевірено", f"Курс «{obj.assignment.lesson.course.title}». Ваша оцінка й особистий коментар доступні у кабінеті.")
    elif label == "classrooms.studygroup":
        if obj.is_active and (not before or before.get("teacher_id") != obj.teacher_id or not before.get("is_active")):
            send([obj.teacher_id], "group_assigned", "Вам призначено навчальну групу", f"Курс «{obj.course.title}». Відкрийте журнал і розклад групи.", "group")
    elif label == "classrooms.groupmembership":
        if obj.is_active and not before.get("is_active") and obj.enrollment.status == "approved" and obj.group.is_active:
            send([obj.group.teacher_id], "group_student_assigned", "До вашої групи додано учня", f"Курс «{obj.group.course.title}». Актуальний склад доступний у журналі.", "group", obj.group_id)
            send([obj.enrollment.student_id], "group_joined", "Вам призначено навчальну групу", f"Курс «{obj.group.course.title}». Розклад і матеріали — у вашій групі.", "group", obj.group_id)
            lessons = obj.group.lessons.using(using).for_student(obj.enrollment.student, current=True).filter(ends_at__gt=timezone.now())
            for lesson in lessons:
                if lesson.status == "scheduled":
                    send([obj.enrollment.student_id], "lesson_created", "Призначено групове заняття", f"{lesson.course.title} · {lesson_when(lesson)}.", "lesson", lesson.pk, f":{lesson.pk}")
                for assignment in lesson.assignments.all():
                    send([obj.enrollment.student_id], "homework_created", "Нове домашнє завдання", f"Курс «{lesson.course.title}». Матеріали доступні у вашому кабінеті.", "assignment", assignment.pk, f":{assignment.pk}")


def record_change(obj, before, after, using):
    with transaction.atomic(using=using):
        cursor, _ = EventCursor.objects.using(using).get_or_create(key=f"{obj._meta.label_lower}:{obj.pk}", defaults={"state": before})
        cursor = EventCursor.objects.using(using).select_for_update().get(pk=cursor.pk)
        if cursor.state == after:
            return
        previous = cursor.state
        cursor.state = after
        cursor.revision += 1
        cursor.save(using=using, update_fields=["state", "revision"])
        emit_events(obj, previous, after, f"{cursor.pk}:{cursor.revision}", using)
