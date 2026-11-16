from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from classrooms.models import GroupMembership, StudyGroup
from courses.models import Course, CoursePackage
from learning.models import Assignment, Submission
from notifications.models import EventCursor, Notification
from scheduling.models import Lesson
from users.models import User

from .models import Enrollment, PackageAccount, PackageUsage, TrialRequest, TrialGroupAdmission
from .trial_models import normalize_phone


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TrialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # The six new catalogue courses are seeded by migrations; the seventh
        # historical course is existing production data, so recreate it only here.
        Course.objects.create(title="Англійська для початківців", slug="trial-history-fixture", subject="english", price=0, is_published=True)
        cls.course = Course.objects.filter(is_published=True).first()
        cls.other_course = Course.objects.filter(is_published=True).exclude(pk=cls.course.pk).first()
        cls.student = User.objects.create_user(username="trial-student", first_name="Марія", email="owner@example.invalid")
        cls.other = User.objects.create_user(username="trial-other")
        cls.teacher = User.objects.create_user(username="trial-teacher", role="teacher")
        cls.other_teacher = User.objects.create_user(username="trial-other-teacher", role="teacher")
        cls.manager = User.objects.create_superuser(username="trial-admin", email="admin@example.invalid", password="Trial-test-admin-123")
        cls.group = StudyGroup.objects.create(name="Trial test group", course=cls.course, teacher=cls.teacher, capacity=2)

    def setUp(self):
        self.url = reverse("trial_request", args=[self.course.pk])
        self.data = {"student_name": "Марія Тест", "level": "7 клас", "age_category": "minor", "guardian_name": "Олена Тест", "email": "contact@example.invalid", "phone": "+380 (67) 123-45-67", "requested_format": "individual", "comment": "Зручно ввечері", "privacy_consent": "on", "guardian_consent": "on", "website": ""}

    def submit(self, **changes):
        return self.client.post(self.url, self.data | changes)

    def make_trial(self, **changes):
        data = {"course": self.course, "student_name": "Тест учень", "level": "7 клас", "is_minor": False,
                "email": "manual@example.invalid", "phone": "+380671111111", "requested_format": "individual", "consent_at": timezone.now()}
        return TrialRequest.objects.create(**(data | changes))

    def open_group(self, stage="forming", **changes):
        return TrialGroupAdmission.objects.create(group=self.group, stage=stage, accepting_until=timezone.localdate()+timedelta(days=10), **changes)

    def approval(self, trial):
        trial.status = "approved"
        trial.teacher = self.teacher
        trial.save()

    def test_all_seven_courses_link_to_trial_without_login(self):
        courses = list(Course.objects.filter(is_published=True))
        self.assertEqual(len(courses), 7)
        for course in courses:
            self.assertContains(self.client.get(course.get_absolute_url()), reverse("trial_request", args=[course.pk]))
            response = self.client.get(reverse("trial_request", args=[course.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual({value for value, _ in response.context["form"].fields["requested_format"].choices}, {"individual", "group"})

    def test_guest_submit_saves_contacts_consent_only(self):
        models = (User, Enrollment, GroupMembership, Lesson, Assignment, Submission, CoursePackage, PackageAccount, PackageUsage)
        before = [model.objects.count() for model in models]
        notification_count = Notification.objects.count()
        response = self.submit()
        self.assertRedirects(response, reverse("trial_success"))
        trial = TrialRequest.objects.get()
        self.assertIsNone(trial.user_id)
        self.assertIsNone(trial.teacher_id)
        self.assertIsNone(trial.group_id)
        self.assertEqual(trial.phone, "+380671234567")
        self.assertEqual(trial.status, "new")
        self.assertTrue(trial.consent_at and trial.guardian_consent_at)
        self.assertEqual(before, [model.objects.count() for model in models])
        self.assertEqual(Notification.objects.count(), notification_count)
        self.assertEqual(len(mail.outbox), 0)

    def test_authenticated_user_prefill_and_link_without_changing_profile(self):
        self.client.force_login(self.student)
        form = self.client.get(self.url).context["form"]
        self.assertEqual(form["student_name"].value(), "Марія")
        self.assertEqual(form["email"].value(), self.student.email)
        self.submit(email="other-contact@example.invalid")
        self.assertEqual(TrialRequest.objects.get().user_id, self.student.pk)
        self.student.refresh_from_db()
        self.assertEqual(self.student.email, "owner@example.invalid")

    def test_unregistered_email_is_never_linked_to_existing_account(self):
        self.submit(email=self.student.email)
        self.assertIsNone(TrialRequest.objects.get().user_id)

    def test_account_existence_does_not_change_public_response(self):
        first = self.submit(email=self.student.email)
        second = self.submit(email="unknown@example.invalid", phone="+380671234568", student_name="Інший учень")
        self.assertEqual((first.status_code, first.url), (second.status_code, second.url))

    def test_minor_needs_guardian_and_both_consents(self):
        for change, field in (({"guardian_name": ""}, "guardian_name"), ({"guardian_consent": ""}, "guardian_consent"), ({"privacy_consent": ""}, "privacy_consent")):
            response = self.submit(**change)
            self.assertIn(field, response.context["form"].errors)
        self.assertFalse(TrialRequest.objects.exists())

    def test_adult_does_not_need_guardian(self):
        self.submit(age_category="adult", guardian_name="", guardian_consent="")
        trial = TrialRequest.objects.get()
        self.assertFalse(trial.is_minor)
        self.assertIsNone(trial.guardian_consent_at)

    def test_required_fields_and_validation(self):
        for field in ("student_name", "level", "age_category", "email", "phone", "requested_format"):
            with self.subTest(field=field):
                from users.models import AccountRequestThrottle
                AccountRequestThrottle.objects.all().delete()
                response = self.submit(**{field: ""})
                self.assertIn(field, response.context["form"].errors)
        self.assertFalse(TrialRequest.objects.exists())

    def test_invalid_contacts_and_oversized_comment(self):
        for data, field in (({"email": "invalid"}, "email"), ({"phone": "abc+12345"}, "phone"), ({"comment": "x"*1501}, "comment")):
            response = self.submit(**data)
            self.assertIn(field, response.context["form"].errors)
        self.assertFalse(TrialRequest.objects.exists())

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone(" +380 (67) 123-45-67 "), "+380671234567")
        for value in ("12", "+1234567890123456", "https://x.invalid", "123+45678"):
            with self.assertRaises(ValidationError):
                normalize_phone(value)

    def test_active_duplicate_normalized_name_email_and_phone(self):
        self.submit()
        response = self.submit(student_name="  МАРІЯ   ТЕСТ ", email="CONTACT@EXAMPLE.INVALID", phone="380671234567", requested_format="group")
        self.assertRedirects(response, reverse("trial_success"))
        self.assertEqual(TrialRequest.objects.count(), 1)

    def test_phone_duplicate_even_if_email_changes(self):
        self.submit()
        self.submit(email="second@example.invalid")
        self.assertEqual(TrialRequest.objects.count(), 1)

    def test_siblings_can_use_same_parent_contacts(self):
        self.submit()
        self.submit(student_name="Інша дитина")
        self.assertEqual(TrialRequest.objects.count(), 2)

    def test_closed_request_does_not_block_new_enquiry(self):
        self.submit()
        trial = TrialRequest.objects.get()
        trial.status = "cancelled"
        trial.save()
        self.submit()
        self.assertEqual(TrialRequest.objects.count(), 2)

    def test_csrf_rejects_direct_post(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, self.data).status_code, 403)
        response = client.get(self.url)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(self.url, self.data | {"csrfmiddlewaretoken": token}).status_code, 302)

    def test_honeypot_is_silent_and_does_not_save(self):
        self.assertRedirects(self.submit(website="spam.invalid"), reverse("trial_success"))
        self.assertFalse(TrialRequest.objects.exists())

    def test_ip_throttle_includes_invalid_posts(self):
        for _ in range(5):
            self.submit(email="bad")
        response = self.submit()
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertFalse(TrialRequest.objects.exists())

    def test_contact_throttle_spans_ips_and_course(self):
        for n in range(3):
            self.client.post(self.url, self.data | {"student_name": str(n)}, REMOTE_ADDR=f"192.0.2.{n}")
        response = self.client.post(self.url, self.data, REMOTE_ADDR="192.0.2.99")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(TrialRequest.objects.count(), 3)

    def test_throttle_storage_contains_no_plain_contact(self):
        self.submit()
        from users.models import AccountRequestThrottle
        keys = list(AccountRequestThrottle.objects.values_list("key", flat=True))
        self.assertNotIn(self.data["email"], str(keys))
        self.assertNotIn(self.data["phone"], str(keys))

    def test_unpublished_and_missing_course_404(self):
        self.course.is_published = False
        self.course.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.submit().status_code, 404)
        self.assertEqual(self.client.get(reverse("trial_request", args=[999999])).status_code, 404)

    def test_course_and_internal_fields_cannot_be_injected(self):
        self.submit(course=self.other_course.pk, user=self.student.pk, status="approved", teacher=self.teacher.pk, group=self.group.pk, consent_version="fake")
        trial = TrialRequest.objects.get()
        self.assertEqual(trial.course, self.course)
        self.assertEqual(trial.status, "new")
        self.assertIsNone(trial.user_id)
        self.assertIsNone(trial.teacher_id)
        self.assertIsNone(trial.group_id)
        self.assertEqual(trial.consent_version, "trial-2026-11-v1")

    def test_disabled_course_format_is_rejected(self):
        self.course.supports_group = False
        self.course.save()
        self.assertIn("requested_format", self.submit(requested_format="group").context["form"].errors)

    def test_individual_approval_requires_teacher_and_creates_no_lesson(self):
        trial = self.make_trial()
        trial.status = "approved"
        with self.assertRaises(ValidationError): trial.save()
        trial.teacher = self.teacher
        trial.save()
        self.assertFalse(Lesson.objects.exists())
        self.assertFalse(Enrollment.objects.exists())
        self.assertFalse(PackageAccount.objects.exists())

    def test_group_enquiry_can_wait_without_group(self):
        self.submit(requested_format="group")
        trial = TrialRequest.objects.get()
        self.assertEqual(trial.requested_format, "group")
        self.assertIsNone(trial.group_id)
        self.assertFalse(GroupMembership.objects.exists())

    def test_group_approval_denied_without_policy_or_level_check(self):
        trial = self.make_trial(requested_format="group", group=self.group, teacher=self.teacher, status="contacted")
        trial.status = "approved"
        with self.assertRaises(ValidationError): trial.save()
        self.open_group()
        with self.assertRaises(ValidationError): trial.save()
        trial.group_level_confirmed = True
        trial.save()
        self.assertFalse(GroupMembership.objects.exists())

    def test_group_closed_expired_archived_or_no_capacity_denies_approval(self):
        policy = self.open_group()
        trial = self.make_trial(requested_format="group", group=self.group, teacher=self.teacher, group_level_confirmed=True)
        trial.status = "approved"
        for field, value in (("stage", "closed"), ("accepting_until", timezone.localdate()-timedelta(days=1))):
            with self.subTest(field=field):
                TrialGroupAdmission.objects.filter(pk=policy.pk).update(**{field: value})
                with self.assertRaises(ValidationError): trial.save()
                TrialGroupAdmission.objects.filter(pk=policy.pk).update(stage="forming", accepting_until=timezone.localdate()+timedelta(days=10))
        # Simulate a legacy group without capacity; the existing model correctly
        # prevents clearing an established limit through ordinary edits.
        StudyGroup.objects.filter(pk=self.group.pk).update(capacity=None)
        with self.assertRaises(ValidationError): trial.save()

    def test_group_course_teacher_and_format_must_match(self):
        for values in ({"course": self.other_course, "requested_format": "group"}, {"requested_format": "individual"}):
            with self.assertRaises(ValidationError): self.make_trial(group=self.group, **values)
        self.open_group()
        with self.assertRaises(ValidationError):
            self.make_trial(group=self.group, requested_format="group", status="approved", group_level_confirmed=True, teacher=self.other_teacher)

    def test_group_trial_approvals_count_free_places(self):
        self.group.capacity = 1
        self.group.save()
        self.open_group("starting")
        self.make_trial(group=self.group, requested_format="group", status="approved", group_level_confirmed=True, teacher=self.teacher)
        with self.assertRaises(ValidationError):
            self.make_trial(student_name="Другий", email="other@example.invalid", phone="+380672222222", group=self.group, requested_format="group", status="approved", group_level_confirmed=True, teacher=self.teacher)

    def test_policy_needs_valid_deadline(self):
        for deadline in (None, timezone.localdate()-timedelta(days=1)):
            with self.assertRaises(ValidationError): TrialGroupAdmission.objects.create(group=self.group, stage="starting", accepting_until=deadline)

    def test_cannot_assign_student_or_staff_as_teacher(self):
        for user in (self.student, self.manager):
            with self.assertRaises(ValidationError): self.make_trial(teacher=user)

    def test_admin_requires_permission_and_can_process_request(self):
        trial = self.make_trial()
        url = reverse("admin:enrollments_trialrequest_change", args=[trial.pk])
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = User.objects.create_user(username="trial-staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertContains(response, trial.email)
        self.assertEqual(self.client.post(url, {"status": "contacted", "teacher": self.teacher.pk, "group": "", "group_level_confirmed": "", "staff_note": "Узгоджуємо дату", "_save": "Save"}).status_code, 302)
        trial.refresh_from_db()
        self.assertEqual(trial.status, "contacted")

    def test_admin_inline_and_invalid_direct_status_post(self):
        trial = self.make_trial(requested_format="group", group=self.group)
        self.client.force_login(self.manager)
        response = self.client.get(reverse("admin:classrooms_studygroup_change", args=[self.group.pk]))
        self.assertContains(response, "Етап пробного набору")
        response = self.client.post(reverse("admin:enrollments_trialrequest_change", args=[trial.pk]), {"status": "approved", "teacher": self.teacher.pk, "group": self.group.pk, "group_level_confirmed": "on", "staff_note": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.non_field_errors())
        trial.refresh_from_db()
        self.assertEqual(trial.status, "new")

    def test_personal_data_not_in_success_urls_notifications_or_audit_repr(self):
        self.client.force_login(self.student)
        response = self.submit()
        trial = TrialRequest.objects.get()
        self.approval(trial)
        success = self.client.get(response.url)
        for value in (trial.email, trial.phone, trial.student_name, trial.guardian_name, trial.comment):
            self.assertNotContains(success, value)
            self.assertNotIn(value, str(trial))
            self.assertFalse(Notification.objects.filter(text__contains=value).exists())
        self.assertEqual(admin.site._registry[TrialRequest].search_fields, ())

    def test_notifications_reuse_existing_channel_and_noop_does_not_duplicate(self):
        trial = self.make_trial(user=self.student)
        self.approval(trial)
        self.assertEqual(Notification.objects.filter(recipient=self.student, kind="trial_status").count(), 1)
        row = Notification.objects.get(recipient=self.teacher, kind="trial_assigned")
        self.assertEqual(row.destination(), reverse("trial_detail", args=[trial.pk]))
        before = Notification.objects.count()
        trial.save()
        self.assertEqual(Notification.objects.count(), before)
        self.assertEqual(len(mail.outbox), 0)
        cursor = EventCursor.objects.get(key=f"enrollments.trialrequest:{trial.pk}")
        self.assertNotIn(trial.email, str(cursor.state))

    def test_detail_access_is_owner_or_assigned_teacher_only(self):
        trial = self.make_trial(user=self.student, teacher=self.teacher, staff_note="Private staff note", comment="Private parent comment")
        url = reverse("trial_detail", args=[trial.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertContains(response, trial.student_name)
        for value in (trial.email, trial.phone, trial.comment, trial.staff_note): self.assertNotContains(response, value)
        self.client.force_login(self.student)
        self.assertContains(self.client.get(url), trial.email)

    def test_teacher_notification_link_falls_back_after_reassignment(self):
        trial = self.make_trial(teacher=self.teacher)
        row = Notification.objects.get(recipient=self.teacher, kind="trial_assigned")
        trial.teacher = self.other_teacher
        trial.save()
        self.assertEqual(row.destination(), reverse("teacher_dashboard"))

    def test_public_views_do_not_cache_contact_forms(self):
        for url in (self.url, reverse("trial_success")):
            self.assertIn("no-store", self.client.get(url)["Cache-Control"])
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_xss_is_escaped_in_form_and_private_detail(self):
        value = '<script>alert("trial")</script>'
        response = self.submit(student_name=value, email="invalid")
        self.assertNotContains(response, value)
        trial = self.make_trial(user=self.student, student_name=value)
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(reverse("trial_detail", args=[trial.pk])), value)

    def test_missing_migration_fails_gracefully(self):
        with patch("enrollments.trial_views.trial_storage_ready", return_value=False):
            self.assertEqual(self.client.get(self.url).status_code, 503)
            self.assertEqual(self.submit().status_code, 503)
        self.assertFalse(TrialRequest.objects.exists())

    def test_existing_member_uses_group_capacity_for_trial_check(self):
        self.group.capacity = 1
        self.group.save()
        enrollment = Enrollment.objects.create(student=self.other, course=self.course, status="approved", requested_format="group", format_is_explicit=True)
        GroupMembership.objects.create(group=self.group, enrollment=enrollment)
        self.open_group()
        with self.assertRaises(ValidationError):
            self.make_trial(requested_format="group", group=self.group, teacher=self.teacher, status="approved", group_level_confirmed=True)

    def test_archived_group_cannot_approve_trial(self):
        self.open_group()
        self.group.is_active = False
        self.group.save()
        with self.assertRaises(ValidationError):
            self.make_trial(requested_format="group", group=self.group, teacher=self.teacher, status="approved", group_level_confirmed=True)

    def test_database_constraint_rejects_active_duplicates_even_without_save(self):
        trial = self.make_trial()
        values = {field.attname: getattr(trial, field.attname) for field in trial._meta.concrete_fields if not field.primary_key}
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrialRequest.objects.bulk_create([TrialRequest(**values)])

    def test_trial_data_and_notifications_rollback_together(self):
        trial = self.make_trial(user=self.student)
        count = Notification.objects.count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.approval(trial)
                raise RuntimeError("rollback fixture")
        trial.refresh_from_db()
        self.assertEqual(trial.status, "new")
        self.assertEqual(Notification.objects.count(), count)

    def test_guest_success_refresh_does_not_submit_again(self):
        response = self.submit()
        for _ in range(3):
            self.assertEqual(self.client.get(response.url).status_code, 200)
        self.assertEqual(TrialRequest.objects.count(), 1)

    def test_admin_can_open_trial_admission_using_existing_group_page(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("admin:classrooms_studygroup_change", args=[self.group.pk]), {
            "name": self.group.name, "course": self.course.pk, "teacher": self.teacher.pk,
            "capacity": 2, "is_active": "on",
            "memberships-TOTAL_FORMS": 0, "memberships-INITIAL_FORMS": 0,
            "memberships-MIN_NUM_FORMS": 0, "memberships-MAX_NUM_FORMS": 1000,
            "trial_admission-TOTAL_FORMS": 1, "trial_admission-INITIAL_FORMS": 0,
            "trial_admission-MIN_NUM_FORMS": 0, "trial_admission-MAX_NUM_FORMS": 1,
            "trial_admission-0-stage": "forming", "trial_admission-0-group": self.group.pk,
            "trial_admission-0-accepting_until": (timezone.localdate()+timedelta(days=10)).isoformat(),
            "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TrialGroupAdmission.objects.get(group=self.group).stage, "forming")
        self.assertFalse(GroupMembership.objects.exists())
