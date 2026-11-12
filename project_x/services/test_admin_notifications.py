from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone

from main.models import Report
from main.tests import ApiTestCase, make_user
from services.models.job_models import BookArtisan, Withdrawal


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AdminNotificationTests(ApiTestCase):
    def setUp(self):
        self.staff = make_user("staff@example.com", is_staff=True)
        self.customer = make_user("chi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN", verification_status="APPROVED", onboardingComplete=True)

    def feed(self, query=""):
        self.client.force_authenticate(self.staff)
        response = self.client.get(f"/api/admin/notifications{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return self.data(response)

    def withdrawal(self, status="PENDING", **extra):
        return Withdrawal.objects.create(artisan=self.artisan, amount=Decimal("5000"), status=status, bank_name="GTBank",
                                         account_number="0123456789", account_name="Ada Test", recipient_code="RCP_1",
                                         reference=f"WD_{Withdrawal.objects.count()}", **extra)

    def test_staff_only(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/notifications").status_code, 403)

    def test_waiting_items_come_first_and_are_counted(self):
        old = timezone.now() - timedelta(days=200)
        waiting = make_user("bayo@example.com", role="ARTISAN", onboardingComplete=True)
        type(waiting).objects.filter(pk=waiting.pk).update(date_joined=old)  # old, but still waiting
        make_user("tunde@example.com", role="ARTISAN")  # still onboarding: nothing for staff yet
        BookArtisan.objects.create(client=self.customer, artisan=self.artisan, description="Sink", budget=9000,
                                   status="Disputed", disputed_at=old, dispute_reason="Still leaking")
        self.withdrawal()
        Report.objects.create(reporter=self.customer, target_type="USER", reported_user=self.artisan, reason="SCAM")

        data = self.feed()
        self.assertEqual(data["needs_action"], 4)
        self.assertEqual(data["needs_action_by_kind"],
                         {"ARTISAN_REVIEW": 1, "PROBLEM_REPORTED": 1, "WITHDRAWAL_REQUESTED": 1, "REPORT": 1})
        self.assertTrue(all(entry["needs_action"] for entry in data["items"][:4]))
        review = next(entry for entry in data["items"] if entry["kind"] == "ARTISAN_REVIEW")
        self.assertEqual(review["link"], {"page": "artisans", "id": str(waiting.pk)})

    def test_settled_things_are_history_within_the_window(self):
        now = timezone.now()
        BookArtisan.objects.create(client=self.customer, artisan=self.artisan, description="Sink", budget=9000,
                                   status="Refunded", disputed_at=now - timedelta(days=3), resolved_at=now,
                                   resolved_by=self.staff, resolution_note="Refunded in full")
        self.withdrawal(status="FAILED", reviewed_at=now, note="Bank rejected it.")
        BookArtisan.objects.create(client=self.customer, artisan=self.artisan, description="Old", budget=9000,
                                   status="Released", disputed_at=now - timedelta(days=60),
                                   resolved_at=now - timedelta(days=59))
        data = self.feed("?days=30")
        kinds = sorted(entry["kind"] for entry in data["items"])
        self.assertEqual(kinds, ["PAYOUT_FAILED", "PROBLEM_REPORTED", "PROBLEM_SETTLED", "WITHDRAWAL_REQUESTED"])
        self.assertEqual(data["needs_action"], 0)
        settled = next(entry for entry in data["items"] if entry["kind"] == "PROBLEM_SETTLED")
        self.assertIn("refunded the customer", settled["title"])
        # A longer window brings the older one back
        self.assertEqual(len(self.feed("?days=90")["items"]), 6)
