from datetime import timedelta
from io import BytesIO
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image

from main.models import Notification, Skill
from main.storage import SpacesStorage
from main.tests import ApiTestCase, make_user
from services.models.job_models import BookArtisan
from services.models.marketplace_models import JobRequest, Offer, RequestAttachment
from services.models.services_models import Services


def future(days=5):
    return str(timezone.localdate() + timedelta(days=days))


def photo(name="sink.png"):
    buffer = BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


# Hashing a password the real way takes a while, and every test makes six users
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MarketplaceTestCase(ApiTestCase):
    """A customer, two approved plumbers in Lagos, an approved tailor and a plumber still awaiting review"""

    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.other_customer = make_user("femi@example.com")
        self.plumbing = Services.objects.create(name="Plumbing", description="Pipes")
        self.tiling = Services.objects.create(name="Tiling", description="Floors")
        self.tailoring = Services.objects.create(name="Tailoring", description="Clothes")
        self.plumber = self.provider("ada@example.com", [self.plumbing, self.tiling])
        self.plumber2 = self.provider("bayo@example.com", [self.plumbing])
        self.tailor = self.provider("kemi@example.com", [self.tailoring])
        self.pending = self.provider("tunde@example.com", [self.plumbing], verification_status="PENDING")

    def provider(self, email, services, **extra):
        user = make_user(email, role="ARTISAN", onboardingComplete=True, state="Lagos", city="Yaba",
                         **{"verification_status": "APPROVED", **extra})
        skill = Skill.objects.create(artisan=user, description="Good work", base_charge=5000, cap_charge=50000)
        skill.services.set(services)
        return user

    def draft(self, **overrides):
        self.client.force_authenticate(self.customer)
        response = self.client.post("/api/requests", {
            "title": "Leaking kitchen sink",
            "services": [self.plumbing.id],
            "description": "Water drips from under the sink whenever the tap runs.",
            "state": "Lagos",
            "city": "Yaba",
            "address": "3 Fadeyi Street, Yaba",
            "budget_min": "10000",
            "budget_max": "25000",
            "deadline": future(),
            **overrides,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return self.data(response)

    def published(self, **overrides):
        job_request = self.draft(**overrides)
        response = self.client.post(f"/api/requests/{job_request['id']}/publish")
        self.assertEqual(response.status_code, 200, response.content)
        return self.data(response)

    def offer(self, job_request, provider=None, **overrides):
        self.client.force_authenticate(provider or self.plumber)
        return self.client.post("/api/offers", {
            "request": job_request["id"],
            "price": "20000",
            "description": "Replace the trap and reseal the joint.",
            "completion_days": 1,
            "materials_included": True,
            **overrides,
        }, format="json")

    def accept(self, offer_id, **overrides):
        self.client.force_authenticate(self.customer)
        return self.client.post(f"/api/offers/{offer_id}/accept", {
            "scheduled_date": future(3), "scheduled_time": "10:00", **overrides,
        }, format="json")


class RequestTests(MarketplaceTestCase):
    def test_a_customer_drafts_and_publishes_a_request(self):
        job_request = self.draft()
        self.assertEqual(job_request["status"], "DRAFT")
        self.assertEqual(job_request["stage"], "DRAFT")
        self.assertEqual(job_request["reference"], f"REQ-{job_request['id']:06d}")

        published = self.client.post(f"/api/requests/{job_request['id']}/publish")
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(self.data(published)["stage"], "PUBLISHED")
        self.assertIsNotNone(self.data(published)["expires_at"])

    def test_publishing_tells_matching_approved_providers_only(self):
        self.published()
        told = set(Notification.objects.filter(message__startswith="New request").values_list("recipient__email", flat=True))
        self.assertEqual(told, {"ada@example.com", "bayo@example.com"})

    def test_an_on_site_request_needs_an_address_to_publish(self):
        job_request = self.draft(address="")
        response = self.client.post(f"/api/requests/{job_request['id']}/publish")
        self.assertEqual(response.status_code, 400)
        self.assertIn("address", response.json()["data"])

    def test_a_remote_request_needs_no_location(self):
        job_request = self.draft(fulfilment="REMOTE", state="", city="", address="")
        response = self.client.post(f"/api/requests/{job_request['id']}/publish")
        self.assertEqual(response.status_code, 200, response.content)

    def test_validation(self):
        self.client.force_authenticate(self.customer)
        response = self.client.post("/api/requests", {
            "title": "Fix", "services": [], "description": "short", "budget_min": "500", "budget_max": "100",
            "deadline": str(timezone.localdate() - timedelta(days=1)),
        }, format="json")
        self.assertEqual(response.status_code, 400)
        errors = response.json()["data"]
        for field in ("title", "services", "description", "deadline"):
            self.assertIn(field, errors)

    def test_only_customers_post_requests(self):
        self.client.force_authenticate(self.plumber)
        response = self.client.post("/api/requests", {"title": "Anything at all"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_customers_only_see_their_own_requests(self):
        job_request = self.draft()
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.client.get(f"/api/requests/{job_request['id']}").status_code, 404)
        self.assertEqual(self.data(self.client.get("/api/requests")), [])

    def test_only_drafts_can_be_deleted(self):
        draft = self.draft()
        self.assertEqual(self.client.delete(f"/api/requests/{draft['id']}").status_code, 204)
        published = self.published()
        self.assertEqual(self.client.delete(f"/api/requests/{published['id']}").status_code, 400)

    def test_editing_an_open_request_tells_providers_who_offered(self):
        job_request = self.published()
        self.offer(job_request)
        self.client.force_authenticate(self.customer)
        response = self.client.patch(f"/api/requests/{job_request['id']}", {"budget_max": "30000"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Notification.objects.filter(recipient=self.plumber, message__contains="updated their request").exists())

    def test_closing_turns_down_the_offers(self):
        job_request = self.published()
        offer_id = self.data(self.offer(job_request))["id"]
        self.client.force_authenticate(self.customer)
        response = self.client.post(f"/api/requests/{job_request['id']}/close", {"reason": "Fixed it myself"}, format="json")
        self.assertEqual(self.data(response)["stage"], "CLOSED")
        self.assertEqual(Offer.objects.get(pk=offer_id).status, "REJECTED")

    def test_an_expired_request_can_be_renewed(self):
        job_request = self.published()
        JobRequest.objects.filter(pk=job_request["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.data(self.client.get(f"/api/requests/{job_request['id']}"))["stage"], "EXPIRED")
        renewed = self.client.post(f"/api/requests/{job_request['id']}/publish")
        self.assertEqual(self.data(renewed)["stage"], "PUBLISHED")


class FeedTests(MarketplaceTestCase):
    def feed(self, provider, query=""):
        self.client.force_authenticate(provider)
        return self.client.get(f"/api/requests/feed{query}")

    def test_providers_see_requests_for_services_they_offer(self):
        job_request = self.published()
        self.draft(title="Still a draft")
        self.assertEqual([item["id"] for item in self.data(self.feed(self.plumber))], [job_request["id"]])
        self.assertEqual(self.data(self.feed(self.tailor)), [])
        self.assertEqual(len(self.data(self.feed(self.tailor, "?all=true"))), 1)

    def test_providers_awaiting_review_cant_see_requests(self):
        self.published()
        self.assertEqual(self.feed(self.pending).status_code, 403)

    def test_providers_dont_see_the_address_or_the_customers_surname(self):
        job_request = self.published()
        item = self.data(self.feed(self.plumber))[0]
        self.assertIsNone(item["address"])
        self.assertEqual(item["customer"], {"first_name": "Chi", "member_since": mock.ANY})
        detail = self.client.get(f"/api/requests/{job_request['id']}")
        self.assertIsNone(self.data(detail)["address"])

    def test_a_private_request_only_reaches_its_provider(self):
        job_request = self.published(visibility="PRIVATE", target_provider=str(self.plumber2.id))
        self.assertEqual(self.data(self.feed(self.plumber)), [])
        self.assertEqual([item["id"] for item in self.data(self.feed(self.plumber2))], [job_request["id"]])
        self.assertTrue(Notification.objects.filter(recipient=self.plumber2, message__contains="asked you for a quote").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.plumber).exists())
        self.client.force_authenticate(self.plumber)
        self.assertEqual(self.client.get(f"/api/requests/{job_request['id']}").status_code, 404)

    def test_filters(self):
        self.published()
        self.published(title="Bathroom tiles", services=[self.tiling.id], state="Oyo", budget_min="5000", budget_max="8000")
        titles = lambda query: {item["title"] for item in self.data(self.feed(self.plumber, query))}
        self.assertEqual(titles("?state=oyo"), {"Bathroom tiles"})
        self.assertEqual(titles(f"?service={self.plumbing.id}"), {"Leaking kitchen sink"})
        self.assertEqual(titles("?budget=10000"), {"Leaking kitchen sink"})
        self.assertEqual(titles("?search=tiles"), {"Bathroom tiles"})

    def test_offer_counts_arent_multiplied_by_services(self):
        job_request = self.published(services=[self.plumbing.id, self.tiling.id])
        self.offer(job_request, self.plumber)
        self.offer(job_request, self.plumber2)
        self.assertEqual(self.data(self.feed(self.plumber))[0]["offer_count"], 2)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.data(self.client.get("/api/requests"))[0]["offer_count"], 2)

    def test_saving_a_request(self):
        job_request = self.published()
        self.client.force_authenticate(self.plumber)
        self.client.post(f"/api/requests/{job_request['id']}/save")
        self.assertEqual(len(self.data(self.feed(self.plumber, "?saved=true"))), 1)
        self.client.delete(f"/api/requests/{job_request['id']}/save")
        self.assertEqual(self.data(self.feed(self.plumber, "?saved=true")), [])


class OfferTests(MarketplaceTestCase):
    def test_a_provider_sends_an_offer_and_the_customer_is_told(self):
        job_request = self.published()
        response = self.offer(job_request)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.data(response)["status"], "SUBMITTED")
        self.assertTrue(Notification.objects.filter(recipient=self.customer, message__contains="sent you an offer").exists())

        self.client.force_authenticate(self.customer)
        self.assertEqual(self.data(self.client.get(f"/api/requests/{job_request['id']}"))["stage"], "RECEIVING_OFFERS")

    def test_one_offer_per_provider(self):
        job_request = self.published()
        self.offer(job_request)
        self.assertEqual(self.offer(job_request).status_code, 400)

    def test_providers_only_offer_on_requests_for_their_services(self):
        job_request = self.published()
        self.assertEqual(self.offer(job_request, self.tailor).status_code, 400)
        self.assertEqual(self.offer(job_request, self.pending).status_code, 403)

    def test_no_offers_on_drafts_or_someone_elses_private_request(self):
        draft = self.draft()
        self.assertEqual(self.offer(draft).status_code, 400)
        private = self.published(visibility="PRIVATE", target_provider=str(self.plumber2.id))
        self.assertEqual(self.offer(private).status_code, 400)
        self.assertEqual(self.offer(private, self.plumber2).status_code, 201)

    def test_a_revision_keeps_the_earlier_terms(self):
        job_request = self.published()
        offer_id = self.data(self.offer(job_request))["id"]
        response = self.client.patch(f"/api/offers/{offer_id}", {"price": "18000", "warranty": "3 months"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.data(response)["revision_count"], 1)

        self.client.force_authenticate(self.customer)
        offers = self.data(self.client.get(f"/api/requests/{job_request['id']}/offers"))
        self.assertEqual(offers[0]["price"], "18000.00")
        self.assertEqual(offers[0]["revisions"][0]["terms"]["price"], "20000.00")
        self.assertEqual(offers[0]["provider"]["name"], "Ada Test")
        self.assertNotIn("contact", offers[0]["provider"])
        self.assertIsNotNone(Offer.objects.get(pk=offer_id).viewed_at)

    def test_withdrawing_then_offering_again(self):
        job_request = self.published()
        offer_id = self.data(self.offer(job_request))["id"]
        self.assertEqual(self.data(self.client.post(f"/api/offers/{offer_id}/withdraw"))["status"], "WITHDRAWN")
        again = self.offer(job_request, price="22000")
        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(self.data(again)["id"], offer_id)
        self.assertEqual(self.data(again)["revision_count"], 1)

    def test_the_customer_can_turn_down_an_offer(self):
        job_request = self.published()
        offer_id = self.data(self.offer(job_request))["id"]
        self.client.force_authenticate(self.customer)
        response = self.client.post(f"/api/offers/{offer_id}/reject", {"reason": "Too far"}, format="json")
        self.assertEqual(self.data(response)["status"], "REJECTED")
        self.assertEqual(self.accept(offer_id).status_code, 400)

    def test_other_customers_cant_answer_offers(self):
        job_request = self.published()
        offer_id = self.data(self.offer(job_request))["id"]
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.client.post(f"/api/offers/{offer_id}/reject").status_code, 404)
        self.assertEqual(self.client.post(f"/api/offers/{offer_id}/accept", {
            "scheduled_date": future(3), "scheduled_time": "10:00"}, format="json").status_code, 404)

    def test_an_expired_offer_cant_be_accepted(self):
        job_request = self.published()
        offer_id = self.data(self.offer(job_request))["id"]
        Offer.objects.filter(pk=offer_id).update(valid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.accept(offer_id).status_code, 400)


class AcceptTests(MarketplaceTestCase):
    def setUp(self):
        super().setUp()
        self.job_request = self.published()
        self.chosen_id = self.data(self.offer(self.job_request, self.plumber, price="20000"))["id"]
        self.other_id = self.data(self.offer(self.job_request, self.plumber2, price="15000"))["id"]

    def test_accepting_creates_a_booking_to_pay_for(self):
        response = self.accept(self.chosen_id)
        self.assertEqual(response.status_code, 201, response.content)
        booking = BookArtisan.objects.get(pk=self.data(response)["booking_id"])
        self.assertEqual((booking.status, booking.agreed_amount, booking.artisan), ("Accepted", 20000, self.plumber))
        self.assertEqual(booking.address, "3 Fadeyi Street, Yaba")
        self.assertEqual(list(booking.services.all()), [self.plumbing])

        job_request = self.data(self.client.get(f"/api/requests/{self.job_request['id']}"))
        self.assertEqual(job_request["stage"], "AWAITING_PAYMENT")
        self.assertEqual(job_request["booking_id"], booking.id)
        bookings = self.data(self.client.get("/api/my_bookings"))
        self.assertEqual(next(item for item in bookings if item["id"] == booking.id)["request_id"], self.job_request["id"])

    def test_the_other_offers_are_turned_down(self):
        self.accept(self.chosen_id)
        other = Offer.objects.get(pk=self.other_id)
        self.assertEqual((other.status, other.closed_by_acceptance), ("REJECTED", True))
        self.assertTrue(Notification.objects.filter(recipient=self.plumber2, message__contains="chose another offer").exists())
        self.client.force_authenticate(self.plumber2)
        self.assertEqual(self.client.patch(f"/api/offers/{self.other_id}", {"price": "1"}, format="json").status_code, 400)

    def test_only_the_chosen_provider_sees_the_address(self):
        self.accept(self.chosen_id)
        self.client.force_authenticate(self.plumber)
        chosen_view = self.data(self.client.get(f"/api/requests/{self.job_request['id']}"))
        self.assertEqual(chosen_view["address"], "3 Fadeyi Street, Yaba")
        self.assertEqual(chosen_view["stage"], "AWAITING_PAYMENT")
        self.client.force_authenticate(self.plumber2)
        other_view = self.data(self.client.get(f"/api/requests/{self.job_request['id']}"))
        self.assertIsNone(other_view["address"])
        self.assertEqual(other_view["stage"], "OFFER_ACCEPTED")

    def test_only_one_offer_can_be_accepted(self):
        self.accept(self.chosen_id)
        self.assertEqual(self.accept(self.other_id).status_code, 400)

    def test_cancelling_the_booking_reopens_the_request(self):
        booking_id = self.data(self.accept(self.chosen_id))["booking_id"]
        response = self.client.post(f"/api/book/{booking_id}/cancel", {"reason": "Too expensive"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

        job_request = self.data(self.client.get(f"/api/requests/{self.job_request['id']}"))
        self.assertEqual(job_request["stage"], "RECEIVING_OFFERS")
        self.assertIsNone(job_request["booking_id"])
        self.assertEqual(Offer.objects.get(pk=self.other_id).status, "SUBMITTED")
        self.assertEqual(Offer.objects.get(pk=self.chosen_id).status, "REJECTED")
        self.assertEqual(self.accept(self.other_id).status_code, 201)

    def test_the_booking_then_follows_the_usual_flow(self):
        booking_id = self.data(self.accept(self.chosen_id))["booking_id"]
        BookArtisan.objects.filter(pk=booking_id).update(status="Paid")
        job_request = self.data(self.client.get(f"/api/requests/{self.job_request['id']}"))
        self.assertEqual(job_request["stage"], "IN_PROGRESS")


@mock.patch.object(SpacesStorage, "url", side_effect=lambda name: f"https://signed.example/{name}")
class AttachmentTests(MarketplaceTestCase):
    def upload(self, job_request, file, **extra):
        self.client.force_authenticate(self.customer)
        return self.client.post(f"/api/requests/{job_request['id']}/attachments", {"file": file, **extra}, format="multipart")

    def test_photos_are_stored_privately(self, _url):
        job_request = self.draft()
        with mock.patch.object(SpacesStorage, "save", return_value="request_media/1/sink.png") as saved:
            response = self.upload(job_request, photo())
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.data(response)["kind"], "IMAGE")
        self.assertEqual(self.data(response)["url"], "https://signed.example/request_media/1/sink.png")
        saved.assert_called_once()

    def test_unsupported_files_are_refused(self, _url):
        job_request = self.draft()
        document = SimpleUploadedFile("notes.pdf", b"%PDF-1.4", content_type="application/pdf")
        self.assertEqual(self.upload(job_request, document).status_code, 400)
        fake = SimpleUploadedFile("fake.png", b"not an image", content_type="image/png")
        self.assertEqual(self.upload(job_request, fake).status_code, 400)

    def test_private_photos_are_hidden_from_providers(self, _url):
        job_request = self.draft()
        with mock.patch.object(SpacesStorage, "save", side_effect=["request_media/a.png", "request_media/b.png"]):
            self.upload(job_request, photo("a.png"))
            self.upload(job_request, photo("b.png"), private="true")
        self.client.post(f"/api/requests/{job_request['id']}/publish")
        self.client.force_authenticate(self.plumber)
        attachments = self.data(self.client.get(f"/api/requests/{job_request['id']}"))["attachments"]
        self.assertEqual([item["url"] for item in attachments], ["https://signed.example/request_media/a.png"])

    def test_removing_a_photo_deletes_the_file(self, _url):
        job_request = self.draft()
        with mock.patch.object(SpacesStorage, "save", return_value="request_media/a.png"):
            attachment_id = self.data(self.upload(job_request, photo()))["id"]
        with mock.patch.object(SpacesStorage, "delete") as deleted:
            response = self.client.delete(f"/api/requests/{job_request['id']}/attachments/{attachment_id}")
        self.assertEqual(response.status_code, 204)
        deleted.assert_called_once_with("request_media/a.png")
        self.assertFalse(RequestAttachment.objects.exists())


IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class RequestChatTests(MarketplaceTestCase):
    def setUp(self):
        super().setUp()
        self.job_request = self.published()
        self.url = f"/api/requests/{self.job_request['id']}/messages"

    def say(self, user, text, provider=None):
        self.client.force_authenticate(user)
        body = {"text": text, **({"provider": str(provider.id)} if provider else {})}
        return self.client.post(self.url, body, format="json")

    def thread(self, user, provider=None):
        self.client.force_authenticate(user)
        return self.client.get(self.url + (f"?provider={provider.id}" if provider else ""))

    def test_a_provider_asks_a_question_before_offering(self):
        self.assertEqual(self.say(self.plumber, "Is the pipe metal or plastic?").status_code, 201)
        reply = self.say(self.customer, "Plastic", provider=self.plumber)
        self.assertEqual(reply.status_code, 201, reply.content)
        texts = [m["text"] for m in self.data(self.thread(self.plumber))["messages"]]
        self.assertEqual(texts, ["Is the pipe metal or plastic?", "Plastic"])

    def test_providers_only_see_their_own_conversation(self):
        self.say(self.plumber, "Question from Ada")
        self.say(self.plumber2, "Question from Bayo")
        self.assertEqual([m["text"] for m in self.data(self.thread(self.plumber2))["messages"]], ["Question from Bayo"])
        self.assertEqual([m["text"] for m in self.data(self.thread(self.customer, self.plumber))["messages"]],
                         ["Question from Ada"])

    def test_the_customer_cant_cold_message_a_provider(self):
        self.assertEqual(self.say(self.customer, "Hi", provider=self.plumber).status_code, 400)
        self.offer(self.job_request)
        self.assertEqual(self.say(self.customer, "Hi", provider=self.plumber).status_code, 201)

    def test_outsiders_and_unapproved_providers_are_kept_out(self):
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.say(self.pending, "Hello").status_code, 403)
        private = self.published(visibility="PRIVATE", target_provider=str(self.plumber2.id))
        self.client.force_authenticate(self.plumber)
        self.assertEqual(self.client.get(f"/api/requests/{private['id']}/messages").status_code, 404)

    def test_providers_see_only_the_customers_first_name(self):
        self.say(self.plumber, "When are you home?")
        self.offer(self.job_request)
        self.say(self.customer, "After 5pm", provider=self.plumber)
        message = self.data(self.thread(self.plumber))["messages"][1]
        self.assertEqual(message["sender_name"], "Chi")
        conversation = self.data(self.client.get("/api/conversations"))[0]
        self.assertEqual((conversation["kind"], conversation["with"]["name"]), ("request", "Chi"))

    def test_conversations_list_request_threads_with_unread_counts(self):
        self.say(self.plumber, "Is the pipe metal?")
        self.say(self.plumber, "And how old is it?")
        self.client.force_authenticate(self.customer)
        conversation = self.data(self.client.get("/api/conversations"))[0]
        self.assertEqual(conversation["request_id"], self.job_request["id"])
        self.assertEqual(conversation["provider_id"], str(self.plumber.id))
        self.assertEqual((conversation["unread"], conversation["can_send"]), (2, True))
        self.assertEqual(conversation["with"]["name"], "Ada Test")
        self.client.post(self.url + "/read", {"provider": str(self.plumber.id)}, format="json")
        self.assertEqual(self.data(self.client.get("/api/conversations"))[0]["unread"], 0)

    def test_accepting_carries_the_chosen_conversation_into_the_booking(self):
        self.say(self.plumber, "I can bring a new trap")
        offer_id = self.data(self.offer(self.job_request))["id"]
        self.say(self.plumber2, "Me too")
        self.offer(self.job_request, self.plumber2)
        booking_id = self.data(self.accept(offer_id))["booking_id"]

        texts = [m["text"] for m in self.data(self.client.get(f"/api/chat/{booking_id}/"))]
        self.assertEqual(texts, ["I can bring a new trap"])
        conversations = self.data(self.client.get("/api/conversations"))
        self.assertEqual([(c["kind"], c["booking_id"] or c["request_id"]) for c in conversations],
                         [("request", self.job_request["id"]), ("booking", booking_id)])
        # The other provider's conversation is read-only now
        self.assertFalse(conversations[0]["can_send"])
        self.assertEqual(self.say(self.plumber2, "Still keen").status_code, 400)

    def test_a_closed_request_is_read_only(self):
        self.say(self.plumber, "Question")
        self.client.force_authenticate(self.customer)
        self.client.post(f"/api/requests/{self.job_request['id']}/close")
        self.assertEqual(self.say(self.plumber, "Another one").status_code, 400)
        self.assertFalse(self.data(self.thread(self.plumber))["can_send"])
