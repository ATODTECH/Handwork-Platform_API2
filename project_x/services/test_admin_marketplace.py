from main.models import Notification, Report
from main.tests import make_user
from services.models.marketplace_models import JobRequest, Offer
from services.test_marketplace import MarketplaceTestCase, future


class AdminMarketplaceTests(MarketplaceTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_user("staff@example.com", is_staff=True)

    def as_staff(self):
        self.client.force_authenticate(self.staff)

    def sent_offer(self, job_request, provider=None):
        response = self.offer(job_request, provider)
        self.assertEqual(response.status_code, 201, response.content)
        return self.data(response)

    def test_only_staff_see_requests(self):
        self.published()
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/requests").status_code, 403)
        self.client.force_authenticate(self.plumber)
        self.assertEqual(self.client.get("/api/admin/requests").status_code, 403)
        self.as_staff()
        self.assertEqual(self.client.get("/api/admin/requests").status_code, 200)

    def test_requests_by_status_with_counts(self):
        open_request = self.published()
        draft = self.draft(title="Broken wardrobe door")
        self.as_staff()
        listed = self.data(self.client.get("/api/admin/requests?status=open"))
        self.assertEqual([item["id"] for item in listed], [open_request["id"]])
        self.assertEqual(listed[0]["customer"]["email"], "chi@example.com")
        self.assertEqual(listed[0]["stage"], "PUBLISHED")
        self.assertEqual([item["id"] for item in self.data(self.client.get("/api/admin/requests?status=draft"))], [draft["id"]])
        counts = self.data(self.client.get("/api/admin/requests/counts"))
        self.assertEqual((counts["all"], counts["open"], counts["draft"], counts["closed"]), (2, 1, 1, 0))

    def test_search_by_reference_title_or_customer(self):
        job_request = self.published()
        other = self.draft(title="Hem three dresses", services=[self.tailoring.id])
        JobRequest.objects.filter(pk=other["id"]).update(customer=self.other_customer)
        self.as_staff()
        for term in (job_request["reference"], "sink", "chi@example"):
            found = self.data(self.client.get(f"/api/admin/requests?search={term}"))
            self.assertEqual([item["id"] for item in found], [job_request["id"]], term)

    def test_detail_has_the_address_and_every_offer(self):
        job_request = self.published()
        offer = self.sent_offer(job_request)
        Report.objects.create(reporter=self.customer, target_type="OFFER", offer_id=offer["id"],
                              reported_user=self.plumber, reason="SCAM")
        self.as_staff()
        detail = self.data(self.client.get(f"/api/admin/requests/{job_request['id']}"))
        self.assertEqual(detail["address"], "3 Fadeyi Street, Yaba")
        self.assertEqual(len(detail["offers"]), 1)
        self.assertEqual(detail["offers"][0]["provider"]["email"], "ada@example.com")
        self.assertEqual(detail["offers"][0]["open_reports"], 1)

    def test_staff_close_a_request_and_everyone_is_told(self):
        job_request = self.published()
        offer = self.sent_offer(job_request)
        self.as_staff()
        self.assertEqual(self.client.post(f"/api/admin/requests/{job_request['id']}/close", {"reason": "x"}).status_code, 400)
        response = self.client.post(f"/api/admin/requests/{job_request['id']}/close",
                                    {"reason": "Selling goods isn't allowed."}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.data(response)["stage"], "CLOSED")
        self.assertEqual(Offer.objects.get(pk=offer["id"]).status, "REJECTED")
        self.assertTrue(Notification.objects.filter(recipient=self.customer, message__contains="Selling goods").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.plumber, message__contains="was removed").exists())
        # Closing twice is refused
        again = self.client.post(f"/api/admin/requests/{job_request['id']}/close", {"reason": "Again, really."})
        self.assertEqual(again.status_code, 400)

    def test_a_booked_request_cant_be_closed(self):
        job_request = self.published()
        offer = self.sent_offer(job_request)
        self.client.force_authenticate(self.customer)
        accepted = self.client.post(f"/api/offers/{offer['id']}/accept",
                                    {"scheduled_date": future(), "scheduled_time": "10:00"}, format="json")
        self.assertEqual(accepted.status_code, 201, accepted.content)
        self.as_staff()
        response = self.client.post(f"/api/admin/requests/{job_request['id']}/close", {"reason": "Breaks rules"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Bookings", response.json()["data"]["detail"])
        self.assertEqual(JobRequest.objects.get(pk=job_request["id"]).status, "ACCEPTED")

    def test_staff_withdraw_an_offer(self):
        job_request = self.published()
        offer = self.sent_offer(job_request)
        self.client.force_authenticate(self.plumber)
        self.assertEqual(self.client.post(f"/api/admin/offers/{offer['id']}/withdraw", {"reason": "Breaks rules"}).status_code, 403)
        self.as_staff()
        response = self.client.post(f"/api/admin/offers/{offer['id']}/withdraw",
                                    {"reason": "Asked the customer to pay by transfer."}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.data(response)["status"], "WITHDRAWN")
        self.assertTrue(Notification.objects.filter(recipient=self.plumber, message__contains="pay by transfer").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.customer, message__contains="removed by our team").exists())
        self.assertEqual(self.client.post(f"/api/admin/offers/{offer['id']}/withdraw", {"reason": "Breaks rules"}).status_code, 400)
