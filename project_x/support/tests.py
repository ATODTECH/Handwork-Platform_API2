from django.core import mail
from django.test import override_settings

from main.models import Notification
from main.tests import ApiTestCase, make_user
from services.models.job_models import BookArtisan
from support.models import SupportTicket


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], EMAIL_IN_BACKGROUND=False)
class SupportTests(ApiTestCase):
    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.other = make_user("femi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN")
        self.staff = make_user("help@example.com", is_staff=True)
        self.booking = BookArtisan.objects.create(client=self.customer, artisan=self.artisan, description="Sink", budget=9000)

    def open_ticket(self, user=None, **overrides):
        self.client.force_authenticate(user or self.customer)
        return self.client.post("/api/support/tickets", {
            "category": "PAYMENT", "subject": "Charged twice", "message": "I was charged twice for my booking.", **overrides,
        }, format="json")

    def test_a_customer_opens_a_ticket_about_their_booking(self):
        response = self.open_ticket(booking=self.booking.id)
        self.assertEqual(response.status_code, 201, response.content)
        ticket = self.data(response)
        self.assertEqual((ticket["status"], ticket["booking"]), ("OPEN", self.booking.id))
        self.assertTrue(ticket["reference"].startswith("HW-"))
        self.assertEqual(len(ticket["messages"]), 1)
        # Someone else's booking can't be attached, and tickets are private
        self.assertEqual(self.open_ticket(self.other, booking=self.booking.id).status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/support/tickets/{ticket['id']}").status_code, 404)

    def test_staff_reply_and_the_customer_is_told(self):
        ticket_id = self.data(self.open_ticket())["id"]
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/support").status_code, 403)

        self.client.force_authenticate(self.staff)
        inbox = self.data(self.client.get("/api/admin/support?status=open"))
        self.assertEqual([item["id"] for item in inbox], [ticket_id])
        self.assertTrue(inbox[0]["unread"])
        response = self.client.post(f"/api/admin/support/{ticket_id}/reply", {"text": "We've refunded the second charge."}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        ticket = self.data(response)
        self.assertEqual((ticket["status"], ticket["assigned_to"]["email"]), ("WAITING", "help@example.com"))
        self.assertTrue(Notification.objects.filter(recipient=self.customer, notification_type="SUPPORT").exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("refunded the second charge", mail.outbox[0].body)

        # The customer sees it as unread, and replying puts it back in the open queue
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.data(self.client.get("/api/support/tickets/unread"))["count"], 1)
        detail = self.data(self.client.get(f"/api/support/tickets/{ticket_id}"))
        self.assertEqual(detail["messages"][-1]["author_name"], "Help from Handwork Expert")
        self.assertEqual(self.data(self.client.get("/api/support/tickets/unread"))["count"], 0)
        reply = self.data(self.client.post(f"/api/support/tickets/{ticket_id}/messages", {"text": "Thanks, got it"}, format="json"))
        self.assertEqual(reply["status"], "OPEN")

    def test_resolving_and_reopening(self):
        ticket_id = self.data(self.open_ticket())["id"]
        self.client.force_authenticate(self.staff)
        resolved = self.data(self.client.post(f"/api/admin/support/{ticket_id}/reply", {"text": "Done.", "then": "RESOLVED"}, format="json"))
        self.assertEqual(resolved["status"], "RESOLVED")
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.data(self.client.post(f"/api/support/tickets/{ticket_id}/messages", {"text": "Not quite"}, format="json"))["status"], "OPEN")

    def test_assign_priority_and_counts(self):
        ticket_id = self.data(self.open_ticket())["id"]
        self.client.force_authenticate(self.staff)
        self.client.post(f"/api/admin/support/{ticket_id}/assign", {"staff": str(self.staff.id)}, format="json")
        self.client.post(f"/api/admin/support/{ticket_id}/update", {"priority": "HIGH"}, format="json")
        counts = self.data(self.client.get("/api/admin/support/counts"))
        self.assertEqual((counts["open"], counts["mine"], counts["unread"]), (1, 1, 1))
        self.assertEqual(self.client.post(f"/api/admin/support/{ticket_id}/assign", {"staff": str(self.customer.id)}, format="json").status_code, 404)
        self.assertEqual(self.data(self.client.get(f"/api/admin/support?search=HW-{ticket_id:05d}"))[0]["priority"], "HIGH")

    def test_open_tickets_show_in_staff_notifications(self):
        self.open_ticket()
        self.client.force_authenticate(self.staff)
        feed = self.data(self.client.get("/api/admin/notifications"))
        self.assertEqual(feed["needs_action_by_kind"].get("SUPPORT"), 1)
