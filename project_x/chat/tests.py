from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from chat.models import Message
from main.tests import ApiTestCase, make_user
from services.models.job_models import BookArtisan
from services.models.services_models import Services
from setup.asgi import application

IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def make_booking(customer, artisan, status="Pending"):
    booking = BookArtisan.objects.create(client=customer, artisan=artisan, description="Leaking sink", status=status)
    booking.services.set([Services.objects.get_or_create(name="Plumbing", defaults={"description": "Pipes"})[0]])
    return booking


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class ChatTests(ApiTestCase):
    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN")
        self.outsider = make_user("femi@example.com")
        self.staff = make_user("staff@example.com", is_staff=True)
        self.booking = make_booking(self.customer, self.artisan)

    def send(self, user, text, booking=None):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/chat/{(booking or self.booking).id}/send/", {"text": text}, format="json")

    def test_both_sides_can_message_from_the_request(self):
        self.assertEqual(self.send(self.customer, "Is Saturday fine?").status_code, 201)
        self.assertEqual(self.send(self.artisan, "Yes, 10am").status_code, 201)
        self.client.force_authenticate(self.customer)
        texts = [m["text"] for m in self.data(self.client.get(f"/api/chat/{self.booking.id}/"))]
        self.assertEqual(texts, ["Is Saturday fine?", "Yes, 10am"])

    def test_outsiders_cant_read_or_send(self):
        self.assertEqual(self.send(self.outsider, "Hi").status_code, 403)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f"/api/chat/{self.booking.id}/").status_code, 403)

    def test_closed_booking_is_read_only(self):
        for status in ("Declined", "Cancelled", "Released", "Refunded"):
            closed = make_booking(self.customer, self.artisan, status=status)
            self.assertEqual(self.send(self.customer, "Hello?", booking=closed).status_code, 400)
        self.assertEqual(self.send(self.customer, "x" * 2001).status_code, 400)
        self.assertEqual(self.send(self.customer, "   ").status_code, 400)

    def test_conversations_show_the_other_person_unread_and_last_message(self):
        quiet = make_booking(self.customer, self.artisan, status="Cancelled")
        self.send(self.artisan, "I can come tomorrow")
        self.send(self.artisan, "Around 10am")

        self.client.force_authenticate(self.customer)
        first, second = self.data(self.client.get("/api/conversations"))
        self.assertEqual(first["booking_id"], self.booking.id)
        self.assertEqual(first["with"]["name"], "Ada Test")
        self.assertEqual((first["unread"], first["can_send"]), (2, True))
        self.assertEqual(first["last_message"], {**first["last_message"], "text": "Around 10am", "mine": False})
        self.assertEqual(first["services"], ["Plumbing"])
        self.assertEqual((second["booking_id"], second["last_message"], second["can_send"]), (quiet.id, None, False))

        self.client.force_authenticate(self.artisan)
        self.assertEqual(self.data(self.client.get("/api/conversations"))[1]["unread"], 0)

    def test_read_marks_only_the_other_persons_messages(self):
        self.send(self.artisan, "On my way")
        self.send(self.customer, "Thanks")
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.data(self.client.post(f"/api/chat/{self.booking.id}/read/"))["updated"], 1)
        self.assertFalse(Message.objects.get(text="Thanks").is_read)
        self.assertEqual(self.data(self.client.get("/api/conversations"))[0]["unread"], 0)

    def test_staff_can_read_but_customers_cant_use_the_admin_view(self):
        self.send(self.customer, "The tap is still leaking")
        self.client.force_authenticate(self.staff)
        messages = self.data(self.client.get(f"/api/admin/bookings/{self.booking.id}/messages"))
        self.assertEqual([m["text"] for m in messages], ["The tap is still leaking"])
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(f"/api/admin/bookings/{self.booking.id}/messages").status_code, 403)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class ChatSocketTests(TransactionTestCase):
    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN")
        self.booking = make_booking(self.customer, self.artisan)

    def connect(self, user, booking):
        return WebsocketCommunicator(application, f"/ws/chat/{booking.id}/?token={AccessToken.for_user(user)}",
                                     headers=[(b"origin", b"http://localhost")])

    def test_live_messages_reach_the_other_side(self):
        @async_to_sync
        async def run():
            customer, artisan = self.connect(self.customer, self.booking), self.connect(self.artisan, self.booking)
            for socket in (customer, artisan):
                await socket.connect()
                self.assertEqual(await socket.receive_json_from(), {"messages": []})
            await customer.send_json_to({"text": "Hello"})
            received = await artisan.receive_json_from()
            self.assertEqual((received["type"], received["message"]["text"]), ("chat.message", "Hello"))
            self.assertEqual(received["message"]["sender"], str(self.customer.id))
            await customer.disconnect()
            await artisan.disconnect()

        run()

    def test_closed_booking_refuses_new_messages(self):
        self.booking.status = "Released"
        self.booking.save()

        @async_to_sync
        async def run():
            socket = self.connect(self.customer, self.booking)
            await socket.connect()
            await socket.receive_json_from()
            await socket.send_json_to({"text": "Hello"})
            self.assertIn("read-only", (await socket.receive_json_from())["error"])
            await socket.disconnect()

        run()
        self.assertFalse(Message.objects.exists())


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RequestChatSocketTests(TransactionTestCase):
    def setUp(self):
        from main.models import Skill
        from services.models.marketplace_models import JobRequest
        self.customer = make_user("chi@example.com")
        self.provider = make_user("ada@example.com", role="ARTISAN", verification_status="APPROVED")
        self.outsider = make_user("bayo@example.com", role="ARTISAN", verification_status="APPROVED")
        plumbing = Services.objects.create(name="Plumbing", description="Pipes")
        Skill.objects.create(artisan=self.provider, description="Pipes").services.set([plumbing])
        self.job_request = JobRequest.objects.create(
            customer=self.customer, title="Leaking sink", description="Drips under the sink all day long",
            visibility="PRIVATE", target_provider=self.provider,
        )
        self.job_request.services.set([plumbing])
        self.job_request.publish()
        self.job_request.save()

    def connect(self, user, provider):
        return WebsocketCommunicator(
            application, f"/ws/requests/{self.job_request.id}/{provider.id}/?token={AccessToken.for_user(user)}",
            headers=[(b"origin", b"http://localhost")],
        )

    def test_live_messages_reach_the_other_side(self):
        @async_to_sync
        async def run():
            customer, provider = self.connect(self.customer, self.provider), self.connect(self.provider, self.provider)
            for socket in (customer, provider):
                await socket.connect()
                self.assertEqual(await socket.receive_json_from(), {"messages": []})
            await provider.send_json_to({"text": "Is it the cold tap?"})
            received = await customer.receive_json_from()
            self.assertEqual((received["type"], received["message"]["text"]), ("chat.message", "Is it the cold tap?"))
            await customer.disconnect()
            await provider.disconnect()

        run()
        self.assertEqual(Message.objects.get().provider, self.provider)

    def test_someone_elses_conversation_is_refused(self):
        @async_to_sync
        async def run():
            socket = self.connect(self.outsider, self.provider)
            await socket.connect()
            self.assertEqual((await socket.receive_json_from())["error"], "Not authorized")
            await socket.disconnect()

        run()
