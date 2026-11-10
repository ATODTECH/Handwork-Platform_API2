from django.test import override_settings
from django.utils import timezone

from chat.models import Message
from main.models import Notification, Rating, Report, Skill
from main.tests import ApiTestCase, make_user
from services.models.job_models import BookArtisan
from services.models.marketplace_models import JobRequest, Offer
from services.models.services_models import Services


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReportTestCase(ApiTestCase):
    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.other_customer = make_user("femi@example.com")
        self.provider = make_user("ada@example.com", role="ARTISAN", verification_status="APPROVED")
        self.other_provider = make_user("bayo@example.com", role="ARTISAN", verification_status="APPROVED")
        self.staff = make_user("staff@example.com", is_staff=True)
        plumbing = Services.objects.create(name="Plumbing", description="Pipes")
        for provider in (self.provider, self.other_provider):
            Skill.objects.create(artisan=provider, description="Pipes").services.set([plumbing])

        self.job_request = JobRequest.objects.create(
            customer=self.customer, title="Leaking sink", description="Drips under the sink all day long",
        )
        self.job_request.services.set([plumbing])
        self.job_request.publish()
        self.job_request.save()
        self.offer = Offer.objects.create(request=self.job_request, provider=self.provider, price=20000,
                                          description="New trap and seal", completion_days=1)
        self.message = Message.objects.create(request=self.job_request, provider=self.provider,
                                              sender=self.provider, text="Pay me on WhatsApp instead")
        self.booking = BookArtisan.objects.create(client=self.customer, artisan=self.provider,
                                                  description="Old job", status="Released")
        self.review = Rating.objects.create(client=self.customer, artisan=self.provider, star=1,
                                            message="Terrible", booking=self.booking)

    def report(self, user, target_type, target_id, reason="SCAM", **extra):
        self.client.force_authenticate(user)
        return self.client.post("/api/reports", {
            "target_type": target_type, "target_id": str(target_id), "reason": reason, **extra,
        }, format="json")

    def resolve(self, report_id, outcome="action", note="Asked to pay outside the platform", **extra):
        self.client.force_authenticate(self.staff)
        return self.client.post(f"/api/admin/reports/{report_id}/resolve",
                                {"outcome": outcome, "note": note, **extra}, format="json")


class ReportingTests(ReportTestCase):
    def test_everything_a_person_can_see_can_be_reported(self):
        cases = [
            (self.customer, "OFFER", self.offer.id, self.provider),
            (self.customer, "MESSAGE", self.message.id, self.provider),
            (self.customer, "BOOKING", self.booking.id, self.provider),
            (self.customer, "USER", self.provider.id, self.provider),
            (self.provider, "REQUEST", self.job_request.id, self.customer),
            (self.provider, "REVIEW", self.review.id, self.customer),
        ]
        for user, target_type, target_id, responsible in cases:
            response = self.report(user, target_type, target_id)
            self.assertEqual(response.status_code, 201, (target_type, response.content))
            self.assertEqual(Report.objects.get(pk=self.data(response)["id"]).reported_user, responsible, target_type)

    def test_what_someone_cant_see_cant_be_reported(self):
        cases = [
            (self.other_customer, "OFFER", self.offer.id),       # not their request
            (self.other_customer, "MESSAGE", self.message.id),   # not their conversation
            (self.other_customer, "BOOKING", self.booking.id),
            (self.customer, "REQUEST", self.job_request.id),     # their own request
            (self.provider, "MESSAGE", self.message.id),         # their own message
            (self.customer, "REVIEW", self.review.id),           # their own review
            (self.customer, "USER", self.customer.id),           # themselves
            (self.customer, "USER", "not-a-uuid"),
            (self.customer, "OFFER", 999999),
        ]
        for user, target_type, target_id in cases:
            self.assertEqual(self.report(user, target_type, target_id).status_code, 404, (user.email, target_type))

    def test_a_private_request_can_only_be_reported_by_its_provider(self):
        self.job_request.visibility, self.job_request.target_provider = "PRIVATE", self.provider
        self.job_request.save()
        self.assertEqual(self.report(self.other_provider, "REQUEST", self.job_request.id).status_code, 404)
        self.assertEqual(self.report(self.provider, "REQUEST", self.job_request.id).status_code, 201)

    def test_other_needs_details(self):
        self.assertEqual(self.report(self.customer, "OFFER", self.offer.id, reason="OTHER").status_code, 400)
        response = self.report(self.customer, "OFFER", self.offer.id, reason="OTHER", details="He was very rude to me")
        self.assertEqual(response.status_code, 201)

    def test_one_open_report_per_person_and_thing(self):
        self.report(self.customer, "OFFER", self.offer.id)
        self.assertEqual(self.report(self.customer, "OFFER", self.offer.id).status_code, 400)

    def test_people_see_their_own_reports_without_staff_notes(self):
        report_id = self.data(self.report(self.customer, "MESSAGE", self.message.id))["id"]
        self.resolve(report_id)
        self.client.force_authenticate(self.customer)
        mine = self.data(self.client.get("/api/reports"))
        self.assertEqual([(item["id"], item["status"]) for item in mine], [(report_id, "ACTIONED")])
        self.assertNotIn("resolution_note", mine[0])
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.data(self.client.get("/api/reports")), [])


class StaffReportTests(ReportTestCase):
    def test_only_staff_see_reports(self):
        self.report(self.customer, "OFFER", self.offer.id)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/reports").status_code, 403)
        self.client.force_authenticate(self.staff)
        reports = self.data(self.client.get("/api/admin/reports?status=open"))
        self.assertEqual(reports[0]["target"]["price"], "20000.00")
        self.assertEqual(reports[0]["reported_user"]["reports_received"], 1)
        counts = self.data(self.client.get("/api/admin/reports/counts"))
        self.assertEqual((counts["open"], counts["open_by_type"]), (1, {"OFFER": 1}))

    def test_hiding_a_message(self):
        report_id = self.data(self.report(self.customer, "MESSAGE", self.message.id, reason="OFF_PLATFORM"))["id"]
        response = self.resolve(report_id, hide=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.data(response)["target"]["text"], "Pay me on WhatsApp instead")

        self.client.force_authenticate(self.customer)
        messages = self.data(self.client.get(
            f"/api/requests/{self.job_request.id}/messages?provider={self.provider.id}"))["messages"]
        self.assertEqual((messages[0]["text"], messages[0]["hidden"]), ("This message was removed by our team.", True))
        conversation = next(c for c in self.data(self.client.get("/api/conversations")) if c["kind"] == "request")
        self.assertEqual(conversation["last_message"]["text"], "This message was removed by our team.")
        self.assertTrue(Notification.objects.filter(recipient=self.provider, message__contains="removed your message").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.customer, message__contains="taken action").exists())

    def test_hiding_a_review(self):
        report_id = self.data(self.report(self.provider, "REVIEW", self.review.id, reason="FAKE"))["id"]
        self.resolve(report_id, note="Never hired this provider", hide=True)
        self.review.refresh_from_db()
        self.assertIsNotNone(self.review.hidden_at)
        self.assertEqual(self.data(self.client.get(f"/api/artisan_rating?artisan={self.provider.id}")), [])

    def test_hiding_a_request_closes_it_and_its_offers(self):
        report_id = self.data(self.report(self.provider, "REQUEST", self.job_request.id))["id"]
        self.resolve(report_id, hide=True)
        self.job_request.refresh_from_db()
        self.offer.refresh_from_db()
        self.assertEqual((self.job_request.status, self.offer.status), ("CLOSED", "REJECTED"))

    def test_hiding_an_offer_withdraws_it(self):
        report_id = self.data(self.report(self.customer, "OFFER", self.offer.id))["id"]
        self.resolve(report_id, hide=True)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, "WITHDRAWN")

    def test_accounts_and_bookings_cant_be_hidden(self):
        report_id = self.data(self.report(self.customer, "USER", self.provider.id))["id"]
        response = self.resolve(report_id, hide=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Users page", response.json()["data"]["detail"])
        self.assertEqual(self.resolve(report_id).status_code, 200)

    def test_reports_about_the_same_thing_are_settled_together(self):
        first = self.data(self.report(self.customer, "USER", self.provider.id))["id"]
        self.data(self.report(self.other_customer, "USER", self.provider.id))
        self.data(self.report(self.customer, "OFFER", self.offer.id))
        response = self.resolve(first, outcome="dismiss", note="No sign of a problem")
        self.assertEqual(response.status_code, 200, response.content)
        statuses = sorted(Report.objects.values_list("target_type", "status"))
        self.assertEqual(statuses, [("OFFER", "OPEN"), ("USER", "DISMISSED"), ("USER", "DISMISSED")])
        self.assertEqual(Notification.objects.filter(message__contains="didn't find anything").count(), 2)

    def test_a_settled_report_cant_be_settled_again(self):
        report_id = self.data(self.report(self.customer, "OFFER", self.offer.id))["id"]
        self.resolve(report_id, outcome="dismiss")
        self.assertEqual(self.resolve(report_id).status_code, 400)

    def test_dismissing_cant_hide(self):
        report_id = self.data(self.report(self.customer, "OFFER", self.offer.id))["id"]
        self.assertEqual(self.resolve(report_id, outcome="dismiss", hide=True).status_code, 400)

    def test_the_overview_counts_open_reports(self):
        self.report(self.customer, "OFFER", self.offer.id)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.data(self.client.get("/api/admin/overview"))["reports"], {"open": 1})
