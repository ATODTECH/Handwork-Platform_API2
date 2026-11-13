from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from contact.models import Contact, Subscribe
from main.tests import ApiTestCase
from support.models import SupportTicket

MESSAGE = {"name": "Bola Visitor", "phoneNumber": "08011112222", "email": "bola@example.com", "message": "Do you work in Abuja?"}


# The throttle counts in the cache; the database cache table doesn't exist in tests
@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CONTACT_FORM_EMAIL="hello@handworkexpert.com",
    EMAIL_IN_BACKGROUND=False,
)
class ContactFormTests(ApiTestCase):
    def setUp(self):
        cache.clear()

    def test_anyone_can_send_it_without_a_key_and_it_goes_to_the_website_inbox(self):
        response = self.client.post("/v2/contact/api/", MESSAGE, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Contact.objects.count(), 1)
        self.assertFalse(SupportTicket.objects.exists())
        email = mail.outbox[-1]
        self.assertEqual((email.to, email.reply_to), (["hello@handworkexpert.com"], ["bola@example.com"]))
        self.assertIn("Do you work in Abuja?", email.body)
        self.assertIn("08011112222", email.body)

    def test_missing_fields_are_refused(self):
        response = self.client.post("/v2/contact/api/", {"name": "Bola"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["data"])
        self.assertFalse(Contact.objects.exists())

    def test_newsletter_sign_up(self):
        self.assertEqual(self.client.post("/v2/contact/subscribe/api/", {"email": "bola@example.com"}, format="json").status_code, 201)
        self.assertEqual(Subscribe.objects.count(), 1)

    def test_one_visitor_is_limited_to_10_an_hour(self):
        for _ in range(10):
            self.assertEqual(self.client.post("/v2/contact/api/", MESSAGE, format="json").status_code, 201)
        self.assertEqual(self.client.post("/v2/contact/api/", MESSAGE, format="json").status_code, 429)
