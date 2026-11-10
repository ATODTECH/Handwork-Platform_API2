import hashlib
import hmac
import json
from decimal import Decimal
from datetime import timedelta
from io import BytesIO
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from rest_framework import serializers as drf_serializers

from main.emails import ResendEmailBackend, ResendError, greeting, send_email
from main.models import BankDetails, Notification, Rating, Skill, User
from main.paystack import PaystackError
from main.storage import PublicSpacesStorage
from services.models.services_models import Category, Services
from services.models.job_models import BookArtisan, Transaction, Withdrawal
from services.serializers.job_serializers import ArtisanBookingSerializers
from services.views.job_process import record_payment

PRIVATE_ARTISAN_FIELDS = {"email", "contact", "nin", "bvn", "document", "document_type", "address", "password"}


def make_user(email, role="USER", password="Secret#123", **extra):
    user = User.objects.create(
        email=email,
        first_name=email.split("@")[0].title(),
        last_name="Test",
        contact=f"+234{abs(hash(email)) % 10**10:010d}",
        role=role,
        **extra,
    )
    user.set_password(password)
    user.save()
    return user


class ApiTestCase(APITestCase):
    """The API's middleware wraps every body as {status, message, data}"""

    def data(self, response):
        return response.json()["data"]


class ArtisanEndpointTests(ApiTestCase):
    def setUp(self):
        self.artisan = make_user(
            "ada@example.com", role="ARTISAN", nin="12345678901", bvn="10987654321", verification_status="APPROVED"
        )
        self.other_artisan = make_user("bola@example.com", role="ARTISAN")
        self.customer = make_user("chi@example.com")

    def test_list_only_shows_public_profile(self):
        response = self.client.get("/api/artisan")
        self.assertEqual(response.status_code, 200)
        for artisan in self.data(response):
            self.assertFalse(PRIVATE_ARTISAN_FIELDS & artisan.keys(), artisan.keys())

    def test_retrieve_is_public_for_others_and_full_for_owner(self):
        url = f"/api/artisan/{self.artisan.id}"

        public = self.data(self.client.get(url))
        self.assertFalse(PRIVATE_ARTISAN_FIELDS & public.keys())

        self.client.force_authenticate(self.customer)
        self.assertFalse(PRIVATE_ARTISAN_FIELDS & self.data(self.client.get(url)).keys())

        self.client.force_authenticate(self.artisan)
        own = self.data(self.client.get(url))
        self.assertEqual(own["nin"], "12345678901")
        self.assertEqual(own["email"], "ada@example.com")
        self.assertNotIn("password", own)

    def test_public_profile_only_gives_a_rough_location(self):
        User.objects.filter(pk=self.artisan.pk).update(latitude="6.605874", longitude="3.349149")
        public = self.data(self.client.get(f"/api/artisan/{self.artisan.id}"))
        self.assertEqual((public["latitude"], public["longitude"]), ("6.61", "3.35"))

        self.client.force_authenticate(self.artisan)
        self.assertEqual(self.data(self.client.get(f"/api/artisan/{self.artisan.id}"))["latitude"], "6.605874")

    def test_service_lists_hide_contact_details_without_signing_in(self):
        User.objects.filter(pk=self.artisan.pk).update(address="3 Fadeyi Street", latitude="6.605874", longitude="3.349149")
        service = Services.objects.create(name="Plumbing", description="Pipes")
        skill = Skill.objects.create(artisan=self.artisan, description="Pipes", base_charge=1, cap_charge=2)
        skill.services.set([service])
        hidden_artisan_skill = Skill.objects.create(artisan=self.other_artisan, description="Not approved")
        hidden_artisan_skill.services.set([service])

        listed = self.data(self.client.get(f"/api/services/{service.id}"))["client"]
        self.assertEqual([artisan["artisan_id"] for artisan in listed], [str(self.artisan.id)])
        self.assertFalse((PRIVATE_ARTISAN_FIELDS | {"latitude", "longitude"}) & listed[0].keys())
        self.assertEqual(listed[0]["services"], [{"id": service.id, "name": "Plumbing"}])

        found = self.client.get("/api/service?search=plumb")
        self.assertEqual(found.status_code, 200)
        artisans = self.data(found)[0]["artisans"]
        self.assertEqual([artisan["id"] for artisan in artisans], [str(self.artisan.id)])
        self.assertFalse(PRIVATE_ARTISAN_FIELDS & artisans[0].keys())

    def test_only_the_artisan_can_update_their_record(self):
        url = f"/api/artisan/{self.artisan.id}"

        self.assertEqual(self.client.patch(url, {"nin": "00000000000"}).status_code, 401)

        self.client.force_authenticate(self.other_artisan)
        self.assertEqual(self.client.patch(url, {"nin": "00000000000"}).status_code, 403)

        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.patch(url, {"nin": "00000000000"}).status_code, 403)

        self.client.force_authenticate(self.artisan)
        self.assertEqual(self.client.patch(url, {"nin": "22222222222"}).status_code, 200)
        self.artisan.refresh_from_db()
        self.assertEqual(self.artisan.nin, "22222222222")

    def test_only_the_artisan_can_delete_their_record(self):
        self.client.force_authenticate(self.other_artisan)
        self.assertEqual(self.client.delete(f"/api/artisan/{self.artisan.id}").status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.artisan.pk).exists())

    def test_update_cannot_change_role_or_password(self):
        self.client.force_authenticate(self.artisan)
        response = self.client.patch(f"/api/artisan/{self.artisan.id}", {"role": "USER", "password": "Hijack#999"})
        self.assertEqual(response.status_code, 200)
        self.artisan.refresh_from_db()
        self.assertEqual(self.artisan.role, "ARTISAN")
        self.assertTrue(self.artisan.check_password("Secret#123"))

    def test_update_can_keep_own_email(self):
        self.client.force_authenticate(self.artisan)
        response = self.client.patch(f"/api/artisan/{self.artisan.id}", {"email": "ada@example.com"})
        self.assertEqual(response.status_code, 200)

    def test_signup_sets_role_and_hashes_password(self):
        response = self.client.post("/api/artisan", {
            "first_name": "Dayo", "last_name": "Test", "email": "dayo@example.com",
            "contact": "+2348011111111", "password": "Secret#123", "role": "USER",
        })
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("password", self.data(response))
        user = User.objects.get(email="dayo@example.com")
        self.assertEqual(user.role, "ARTISAN")
        self.assertTrue(user.check_password("Secret#123"))

    def test_signup_requires_password(self):
        response = self.client.post("/api/artisan", {
            "first_name": "Eze", "last_name": "Test", "email": "eze@example.com", "contact": "+2348022222222",
        })
        self.assertEqual(response.status_code, 400)


class UserEndpointTests(ApiTestCase):
    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.other = make_user("dee@example.com")

    def test_listing_users_needs_staff(self):
        self.assertEqual(self.client.get("/api/user").status_code, 401)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/user").status_code, 403)

    def test_users_can_only_read_and_edit_themselves(self):
        self.assertEqual(self.client.get(f"/api/user/{self.customer.id}").status_code, 401)

        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/user/{self.customer.id}").status_code, 403)
        self.assertEqual(self.client.patch(f"/api/user/{self.customer.id}", {"first_name": "X"}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/user/{self.customer.id}").status_code, 403)

        self.client.force_authenticate(self.customer)
        own = self.client.get(f"/api/user/{self.customer.id}")
        self.assertEqual(own.status_code, 200)
        self.assertNotIn("password", self.data(own))

    def test_update_cannot_change_role_or_password(self):
        self.client.force_authenticate(self.customer)
        response = self.client.patch(f"/api/user/{self.customer.id}", {"role": "ARTISAN", "password": "Hijack#999"})
        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.role, "USER")
        self.assertTrue(self.customer.check_password("Secret#123"))

    def test_signup_forces_customer_role(self):
        response = self.client.post("/api/user", {
            "first_name": "Femi", "last_name": "Test", "email": "femi@example.com",
            "contact": "+2348033333333", "password": "Secret#123", "role": "ARTISAN",
        })
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(User.objects.get(email="femi@example.com").role, "USER")


class BankDetailTests(ApiTestCase):
    def setUp(self):
        self.artisan = make_user("ada@example.com", role="ARTISAN")
        self.other_artisan = make_user("bola@example.com", role="ARTISAN")
        self.other_bank = BankDetails.objects.create(
            artisan=self.other_artisan, name="Other Bank", account_number="0000000001", account_name="Bola Test",
        )

    def test_list_only_returns_own_bank_details(self):
        BankDetails.objects.create(artisan=self.artisan, name="My Bank", account_number="1111111111", account_name="Ada Test")
        self.client.force_authenticate(self.artisan)
        names = [bank["name"] for bank in self.data(self.client.get("/api/bank_details"))]
        self.assertEqual(names, ["My Bank"])

    def test_cannot_update_someone_elses_bank_details(self):
        self.client.force_authenticate(self.artisan)
        response = self.client.put(f"/api/bank_details/{self.other_bank.id}", {"account_number": "9999999999"})
        self.assertEqual(response.status_code, 404)
        self.other_bank.refresh_from_db()
        self.assertEqual(self.other_bank.account_number, "0000000001")

    @mock.patch("main.serializers.auth_serializers.create_recipient", return_value="RCP_1")
    @mock.patch("main.serializers.auth_serializers.resolve_account", return_value="ADA TEST")
    @mock.patch("main.serializers.auth_serializers.bank_code_for", return_value="058")
    def test_create_always_belongs_to_the_signed_in_artisan(self, *_):
        self.client.force_authenticate(self.artisan)
        response = self.client.post("/api/bank_details", {
            "name": "My Bank", "account_number": "1111111111", "account_name": "Ada Test",
            "artisan": str(self.other_artisan.id),
        })
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(BankDetails.objects.filter(artisan=self.artisan, name="My Bank").exists())

    @mock.patch("main.serializers.auth_serializers.create_recipient", return_value="RCP_1")
    @mock.patch("main.serializers.auth_serializers.resolve_account", return_value="ADA OKAFOR TEST")
    def test_account_name_comes_from_the_bank(self, resolve, _):
        self.client.force_authenticate(self.artisan)
        response = self.client.post("/api/bank_details", {
            "name": "GTBank", "bank_code": "058", "account_number": "1111111111", "account_name": "Someone Else",
        })
        self.assertEqual(response.status_code, 201, response.content)
        bank = BankDetails.objects.get(artisan=self.artisan)
        self.assertEqual((bank.account_name, bank.bank_code, bank.recipient_code), ("ADA OKAFOR TEST", "058", "RCP_1"))
        self.assertTrue(self.data(response)["verified"])
        resolve.assert_called_once_with("1111111111", "058")

    @mock.patch("main.serializers.auth_serializers.resolve_account", side_effect=PaystackError("Could not resolve account name."))
    def test_accounts_paystack_cant_find_are_refused(self, _):
        self.client.force_authenticate(self.artisan)
        response = self.client.post("/api/bank_details", {"name": "GTBank", "bank_code": "058", "account_number": "1111111111"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not resolve", str(response.content))
        self.assertFalse(BankDetails.objects.filter(artisan=self.artisan).exists())


class RatingAndLogsTests(ApiTestCase):
    def setUp(self):
        self.artisan = make_user("ada@example.com", role="ARTISAN")
        self.customer = make_user("chi@example.com")
        self.other_customer = make_user("dee@example.com")
        self.booking = BookArtisan.objects.create(client=self.customer, artisan=self.artisan,
                                                  description="Leaking sink", status="Released")

    def test_ratings_are_written_as_the_signed_in_customer(self):
        payload = {"booking": self.booking.id, "star": 5, "message": "Great", "client": str(self.other_customer.id)}
        self.assertEqual(self.client.post("/api/artisan_rating", payload).status_code, 401)

        self.client.force_authenticate(self.artisan)
        self.assertEqual(self.client.post("/api/artisan_rating", payload).status_code, 403)

        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post("/api/artisan_rating", payload).status_code, 201)
        self.assertEqual(Rating.objects.get().client, self.customer)

    def test_only_the_author_can_change_a_rating(self):
        rating = Rating.objects.create(client=self.customer, artisan=self.artisan, star=4, message="Good", booking=self.booking)
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.client.patch(f"/api/artisan_rating/{rating.id}", {"star": 1}).status_code, 403)

        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.patch(f"/api/artisan_rating/{rating.id}", {"star": 5}).status_code, 200)
        # Reviews stay on record: nobody deletes them (staff hide them instead)
        self.assertEqual(self.client.delete(f"/api/artisan_rating/{rating.id}").status_code, 405)

    def test_ratings_are_public_to_read(self):
        self.assertEqual(self.client.get("/api/artisan_rating").status_code, 200)


class ArtisanVerificationTests(ApiTestCase):
    def setUp(self):
        self.staff = make_user("staff@example.com", is_staff=True)
        self.customer = make_user("chi@example.com")
        self.pending = make_user(
            "ada@example.com", role="ARTISAN", nin="12345678901", onboardingComplete=True, state="Lagos", city="Ikeja"
        )
        self.incomplete = make_user("bola@example.com", role="ARTISAN")
        self.approved = make_user("eze@example.com", role="ARTISAN", onboardingComplete=True, verification_status="APPROVED")

    def review(self, artisan, decision, **data):
        return self.client.post(f"/api/admin/artisans/{artisan.id}/{decision}", data)

    def test_review_endpoints_are_staff_only(self):
        self.assertEqual(self.client.get("/api/admin/artisans").status_code, 401)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/artisans").status_code, 403)
        self.assertEqual(self.review(self.pending, "approve").status_code, 403)

    def test_lists_and_counts_by_status(self):
        self.client.force_authenticate(self.staff)
        pending = self.data(self.client.get("/api/admin/artisans?status=pending"))
        self.assertEqual([artisan["id"] for artisan in pending], [str(self.pending.id)])
        self.assertEqual(pending[0]["nin"], "12345678901")
        counts = self.data(self.client.get("/api/admin/artisans/counts"))
        self.assertEqual(counts, {"incomplete": 1, "pending": 1, "approved": 1, "rejected": 0})

    def test_approve(self):
        self.client.force_authenticate(self.staff)
        response = self.review(self.pending, "approve")
        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.verification_status, "APPROVED")
        self.assertEqual(self.pending.verified_by, self.staff)
        self.assertTrue(Notification.objects.filter(recipient=self.pending, notification_type="VERIFICATION").exists())

    def test_cannot_approve_before_onboarding_is_finished(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.review(self.incomplete, "approve").status_code, 400)

    def test_cannot_approve_without_a_location(self):
        self.pending.city = None
        self.pending.save(update_fields=["city"])
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.review(self.pending, "approve").status_code, 400)

    def test_reject_needs_a_reason_and_resubmitting_returns_to_review(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.review(self.pending, "reject").status_code, 400)
        self.assertEqual(self.review(self.pending, "reject", reason="The ID photo is blurry.").status_code, 200)
        self.pending.refresh_from_db()
        self.assertEqual((self.pending.verification_status, self.pending.verification_note), ("REJECTED", "The ID photo is blurry."))

        self.client.force_authenticate(self.pending)
        own = self.data(self.client.get(f"/api/artisan/{self.pending.id}"))
        self.assertEqual(own["verification_note"], "The ID photo is blurry.")
        self.client.patch(f"/api/artisan/{self.pending.id}", {"nin": "22222222222"})
        self.pending.refresh_from_db()
        self.assertEqual((self.pending.verification_status, self.pending.verification_note), ("PENDING", ""))

    def test_artisans_cannot_approve_themselves(self):
        self.client.force_authenticate(self.pending)
        self.client.patch(f"/api/artisan/{self.pending.id}", {"verification_status": "APPROVED"})
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.verification_status, "PENDING")

    def test_customers_only_see_approved_artisans(self):
        ids = [artisan["id"] for artisan in self.data(self.client.get("/api/artisan"))]
        self.assertEqual(ids, [str(self.approved.id)])
        self.assertEqual(self.client.get(f"/api/artisan/{self.pending.id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/artisan/{self.approved.id}").status_code, 200)

    def test_unapproved_artisans_cannot_be_booked(self):
        serializer = ArtisanBookingSerializers()
        with self.assertRaises(drf_serializers.ValidationError):
            serializer.validate_artisan(self.pending)
        self.assertEqual(serializer.validate_artisan(self.approved), self.approved)

    def test_sign_in_reports_staff(self):
        response = self.client.post("/signin/client", {"email": "staff@example.com", "password": "Secret#123"})
        self.assertTrue(self.data(response)["is_staff"])

    def test_catalogue_is_read_only_for_non_staff(self):
        self.assertEqual(self.client.get("/api/category").status_code, 200)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post("/api/category", {"name": "Spam"}).status_code, 403)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post("/api/category", {"name": "Cleaning"}).status_code, 201)


class BookingTestCase(ApiTestCase):
    """A customer, and an approved artisan who offers plumbing"""

    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.other_customer = make_user("femi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN", onboardingComplete=True, verification_status="APPROVED")
        self.plumbing = Services.objects.create(name="Plumbing", description="Pipes")
        self.tailoring = Services.objects.create(name="Tailoring", description="Clothes")
        skill = Skill.objects.create(
            artisan=self.artisan, description="Leaks and installs", base_charge=5000, cap_charge=20000, weekdays_and_weekends=True
        )
        skill.services.set([self.plumbing])

    def book(self, **overrides):
        return self.client.post("/api/book/artisan", {
            "artisan": str(self.artisan.id),
            "services": [self.plumbing.id],
            "description": "Kitchen sink is leaking",
            "budget": "15000",
            "address": "3 Fadeyi Street, Yaba",
            "scheduled_date": str(timezone.localdate() + timedelta(days=4)),
            "scheduled_time": "10:00",
            **overrides,
        }, format="json")

    def booking(self, **extra):
        self.client.force_authenticate(self.customer)
        booking_id = self.data(self.book())["id"]
        booking = self.customer.job_owner.get(pk=booking_id)
        for field, value in extra.items():
            setattr(booking, field, value)
        booking.save()
        self.client.force_authenticate(self.artisan)
        return booking

    def respond(self, booking, **data):
        return self.client.patch(f"/api/book/artisan/{booking.id}", data, format="json")


class BookingTests(BookingTestCase):
    def test_the_customer_is_whoever_is_signed_in(self):
        self.client.force_authenticate(self.customer)
        response = self.book(client=str(self.other_customer.id))
        self.assertEqual(response.status_code, 201)
        booking = self.customer.job_owner.get()
        self.assertEqual((booking.client, booking.artisan, booking.status), (self.customer, self.artisan, "Pending"))
        self.assertFalse(self.other_customer.job_owner.exists())

    def test_only_signed_in_customers_can_book(self):
        self.assertEqual(self.book().status_code, 403)
        self.client.force_authenticate(self.artisan)
        self.assertEqual(self.book().status_code, 403)

    def test_services_must_be_ones_the_artisan_offers(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.book(services=[self.tailoring.id]).status_code, 400)

    def test_artisan_accepts_with_a_price(self):
        booking = self.booking()
        self.assertEqual(self.respond(booking, status="Accepted").status_code, 400)
        self.assertEqual(self.respond(booking, status="Accepted", agreed_amount="20000").status_code, 200)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.agreed_amount, booking.deposit_amount), ("Accepted", 20000, 14000))

    def test_artisan_can_decline(self):
        booking = self.booking()
        self.assertEqual(self.respond(booking, status="Declined").status_code, 200)
        self.assertTrue(Notification.objects.filter(recipient=self.customer, message__contains="can't take").exists())

    def test_artisans_cannot_skip_payments(self):
        booking = self.booking(status="Accepted", agreed_amount=20000)
        for skipped in ("Paid", "Deposit Paid", "In Progress", "Released", "Paid in Full"):
            self.assertEqual(self.respond(booking, status=skipped).status_code, 400)
        booking = self.booking(status="Deposit Paid", agreed_amount=20000)
        self.assertEqual(self.respond(booking, status="In Progress", agreed_amount="1").status_code, 400)
        self.assertEqual(self.respond(booking, status="In Progress").status_code, 200)
        self.assertEqual(self.respond(booking, status="Completed").status_code, 200)

    def test_customers_cannot_change_a_booking(self):
        booking = self.booking()
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.respond(booking, status="Accepted", agreed_amount="1").status_code, 403)


class PaymentTests(BookingTestCase):
    """One full payment, held until the customer confirms the job is done"""

    def accepted(self):
        return self.booking(status="Accepted", agreed_amount=20000)

    def as_customer(self):
        self.client.force_authenticate(self.customer)

    def test_customer_pays_the_full_price(self):
        booking = self.accepted()
        self.as_customer()
        initialize = {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/x", "access_code": "ac_1", "reference": "r"}}
        with mock.patch("services.views.job_process.paystack.transaction.initialize", return_value=initialize) as start:
            response = self.client.post(f"/api/book/{booking.id}/pay")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.data(response)["access_code"], "ac_1")
        self.assertEqual(start.call_args.kwargs["amount"], 2_000_000)
        self.assertTrue(start.call_args.kwargs["reference"].startswith(f"full_{booking.id}_"))

    def test_only_accepted_bookings_can_be_paid(self):
        booking = self.booking()
        self.as_customer()
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/pay").status_code, 400)

    def test_a_payment_is_applied_once_and_only_for_the_right_amount(self):
        booking = self.accepted()
        record_payment(f"full_{booking.id}_1", 100)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "Accepted")

        record_payment(f"full_{booking.id}_1", 2_000_000)
        record_payment(f"full_{booking.id}_1", 2_000_000)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "Paid")
        self.assertEqual(Notification.objects.filter(recipient=self.artisan, message__contains="has paid").count(), 1)

    def test_verify_after_the_popup(self):
        booking = self.accepted()
        self.as_customer()
        reference = f"full_{booking.id}_1"
        with mock.patch("services.views.job_process.verify_paystack", return_value={"amount": 2_000_000}):
            response = self.client.post(f"/api/book/{booking.id}/verify-payment", {"reference": reference}, format="json")
        self.assertEqual(self.data(response)["status"], "Paid")
        with mock.patch("services.views.job_process.verify_paystack", return_value=None):
            other = self.accepted()
            self.as_customer()
            self.assertEqual(self.client.post(f"/api/book/{other.id}/verify-payment", {"reference": f"full_{other.id}_2"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/verify-payment", {"reference": "full_999_1"}, format="json").status_code, 400)

    def test_signed_webhook_records_the_payment(self):
        booking = self.accepted()
        body = json.dumps({"event": "charge.success", "data": {"reference": f"full_{booking.id}_1", "amount": 2_000_000}}).encode()
        signature = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
        self.client.post("/api/paystack/webhook/", body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE="bad")
        booking.refresh_from_db()
        self.assertEqual(booking.status, "Accepted")
        self.client.post("/api/paystack/webhook/", body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "Paid")

    def test_confirming_pays_the_artisan_once(self):
        booking = self.booking(status="Paid", agreed_amount=20000)
        self.assertEqual(self.respond(booking, status="In Progress").status_code, 200)
        self.assertEqual(self.respond(booking, status="Completed").status_code, 200)
        booking.refresh_from_db()
        self.assertIsNotNone(booking.completed_at)

        self.as_customer()
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/pay-balance/").status_code, 400)
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/confirm").status_code, 200)
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/confirm").status_code, 400)
        booking.refresh_from_db()
        self.artisan.refresh_from_db()
        self.assertEqual(booking.status, "Released")
        self.assertEqual(self.artisan.wallet_balance, 18000)
        self.assertEqual(Transaction.objects.get(booking=booking).net_amount, 18000)

    def test_unconfirmed_jobs_are_released_after_48_hours(self):
        old = self.booking(status="Completed", agreed_amount=20000, completed_at=timezone.now() - timedelta(hours=49))
        recent = self.booking(status="Completed", agreed_amount=20000, completed_at=timezone.now() - timedelta(hours=1))
        call_command("release_completed_bookings", stdout=mock.MagicMock())
        old.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual((old.status, recent.status), ("Released", "Completed"))


class BookingLifecycleTests(BookingTestCase):
    """Cancelling before paying, phone numbers once paid, and problems staff settle"""

    def as_customer(self):
        self.client.force_authenticate(self.customer)

    def test_past_dates_are_refused(self):
        self.as_customer()
        yesterday = str(timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.book(scheduled_date=yesterday).status_code, 400)

    def test_accepting_tells_the_customer_the_price_against_their_budget(self):
        booking = self.booking()
        self.respond(booking, status="Accepted", agreed_amount="20000")
        message = Notification.objects.filter(recipient=self.customer, notification_type="PAYMENT").latest("id").message
        self.assertIn("₦20,000", message)
        self.assertIn("budget was ₦15,000", message)
        self.assertNotIn("deposit", message)

    def test_customer_can_cancel_or_turn_down_the_price_before_paying(self):
        for status in ("Pending", "Accepted"):
            booking = self.booking(status=status, agreed_amount=20000 if status == "Accepted" else None)
            self.as_customer()
            response = self.client.post(f"/api/book/{booking.id}/cancel", {"reason": "Too expensive"}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.data(response)["status"], "Cancelled")
        self.assertEqual(Notification.objects.filter(recipient=self.artisan, message__contains="declined your price").count(), 1)

    def test_paid_or_other_peoples_bookings_cannot_be_cancelled(self):
        paid = self.booking(status="Paid", agreed_amount=20000)
        self.as_customer()
        self.assertEqual(self.client.post(f"/api/book/{paid.id}/cancel").status_code, 400)
        pending = self.booking()
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.client.post(f"/api/book/{pending.id}/cancel").status_code, 404)

    def test_phone_numbers_are_shared_once_paid(self):
        booking = self.booking(status="Accepted", agreed_amount=20000)
        self.as_customer()
        self.assertIsNone(self.data(self.client.get(f"/api/book/artisan/{booking.id}"))["contact"])
        booking.status = "Paid"
        booking.save()
        self.assertEqual(self.data(self.client.get(f"/api/book/artisan/{booking.id}"))["contact"], self.artisan.contact)
        self.client.force_authenticate(self.artisan)
        self.assertEqual(self.data(self.client.get(f"/api/book/artisan/{booking.id}"))["contact"], self.customer.contact)

    def test_reporting_a_problem_stops_the_automatic_payout(self):
        booking = self.booking(status="Completed", agreed_amount=20000, completed_at=timezone.now() - timedelta(hours=49))
        self.as_customer()
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/report", {"reason": "short"}, format="json").status_code, 400)
        response = self.client.post(f"/api/book/{booking.id}/report", {"reason": "The sink still leaks"}, format="json")
        self.assertEqual(self.data(response)["status"], "Disputed")
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/confirm").status_code, 400)
        call_command("release_completed_bookings", stdout=mock.MagicMock())
        booking.refresh_from_db()
        self.assertEqual(booking.status, "Disputed")
        self.assertTrue(Notification.objects.filter(recipient=self.artisan, message__contains="reported a problem").exists())

    def test_problems_cannot_be_reported_before_paying(self):
        booking = self.booking(status="Accepted", agreed_amount=20000)
        self.as_customer()
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/report", {"reason": "The sink still leaks"}, format="json").status_code, 400)

    def disputed(self, **extra):
        return self.booking(status="Disputed", agreed_amount=20000, dispute_reason="Still leaks", **extra)

    def test_bookings_admin_is_staff_only(self):
        self.as_customer()
        self.assertEqual(self.client.get("/api/admin/bookings").status_code, 403)

    def test_staff_list_and_count_bookings(self):
        self.disputed()
        self.booking()
        self.client.force_authenticate(make_user("staff@example.com", is_staff=True))
        self.assertEqual(len(self.data(self.client.get("/api/admin/bookings?status=disputed"))), 1)
        counts = self.data(self.client.get("/api/admin/bookings/counts"))
        self.assertEqual((counts["all"], counts["disputed"], counts["requested"]), (2, 1, 1))

    def test_staff_settle_a_problem_by_paying_the_artisan(self):
        booking = self.disputed()
        self.client.force_authenticate(make_user("staff@example.com", is_staff=True))
        self.assertEqual(self.client.post(f"/api/admin/bookings/{booking.id}/resolve", {"outcome": "release"}, format="json").status_code, 400)
        response = self.client.post(f"/api/admin/bookings/{booking.id}/resolve", {"outcome": "release", "note": "Fixed on a revisit"}, format="json")
        self.assertEqual(self.data(response)["status"], "Released")
        self.artisan.refresh_from_db()
        self.assertEqual(self.artisan.wallet_balance, 18000)
        again = self.client.post(f"/api/admin/bookings/{booking.id}/resolve", {"outcome": "refund", "note": "x"}, format="json")
        self.assertEqual(again.status_code, 400)

    @mock.patch("services.views.admin_bookings.refund_charge", return_value={"status": "pending"})
    def test_staff_settle_a_problem_with_a_refund(self, refund):
        booking = self.disputed(payment_reference="full_1_1")
        self.client.force_authenticate(make_user("staff@example.com", is_staff=True))
        response = self.client.post(f"/api/admin/bookings/{booking.id}/resolve", {"outcome": "refund", "note": "No-show"}, format="json")
        self.assertEqual(self.data(response)["status"], "Refunded")
        refund.assert_called_once_with("full_1_1")
        self.artisan.refresh_from_db()
        self.assertEqual(self.artisan.wallet_balance, 0)

    @mock.patch("services.views.admin_bookings.refund_charge", side_effect=PaystackError("Insufficient balance"))
    def test_a_refund_paystack_refuses_leaves_it_open(self, _):
        booking = self.disputed(payment_reference="full_1_1")
        self.client.force_authenticate(make_user("staff@example.com", is_staff=True))
        response = self.client.post(f"/api/admin/bookings/{booking.id}/resolve", {"outcome": "refund", "note": "No-show"}, format="json")
        self.assertEqual(response.status_code, 400)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "Disputed")


class WithdrawalTests(ApiTestCase):
    """Artisans withdraw from their wallet; staff approve; Paystack sends it"""

    def setUp(self):
        self.staff = make_user("staff@example.com", is_staff=True)
        self.artisan = make_user("ada@example.com", role="ARTISAN", onboardingComplete=True, verification_status="APPROVED")
        self.artisan.wallet_balance = Decimal("20000")
        self.artisan.save()
        self.bank = BankDetails.objects.create(
            artisan=self.artisan, name="GTBank", bank_code="058", account_number="1111111111",
            account_name="ADA TEST", recipient_code="RCP_1",
        )

    def withdraw(self, amount="5000"):
        self.client.force_authenticate(self.artisan)
        return self.client.post("/api/withdrawals", {"amount": amount}, format="json")

    def approve(self, withdrawal, transfer=None, error=None):
        self.client.force_authenticate(self.staff)
        with mock.patch("services.views.payouts.send_transfer", return_value=transfer, side_effect=error) as send:
            response = self.client.post(f"/api/admin/withdrawals/{withdrawal.id}/approve")
        return response, send

    def webhook(self, event, reference):
        body = json.dumps({"event": event, "data": {"reference": reference}}).encode()
        signature = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
        self.client.post("/api/paystack/webhook/", body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)

    def balance(self):
        self.artisan.refresh_from_db()
        return self.artisan.wallet_balance

    def test_requesting_holds_the_money(self):
        self.assertEqual(self.withdraw().status_code, 201)
        self.assertEqual(self.balance(), 15000)
        withdrawal = Withdrawal.objects.get()
        self.assertEqual((withdrawal.status, withdrawal.recipient_code), ("PENDING", "RCP_1"))
        self.assertTrue(withdrawal.reference.startswith("withdrawal_"))

    def test_limits(self):
        self.assertEqual(self.withdraw("500").status_code, 400)
        self.assertEqual(self.withdraw("25000").status_code, 400)
        self.assertEqual(self.withdraw("5000").status_code, 201)
        self.assertEqual(self.withdraw("1000").status_code, 400)
        self.assertEqual(self.balance(), 15000)

    def test_customers_cannot_withdraw(self):
        self.client.force_authenticate(make_user("chi@example.com"))
        self.assertEqual(self.client.post("/api/withdrawals", {"amount": "5000"}).status_code, 403)

    def test_old_bank_details_are_verified_on_first_withdrawal(self):
        self.bank.bank_code, self.bank.recipient_code = "", ""
        self.bank.save()
        with mock.patch("services.views.payouts.bank_code_for", return_value="058"), \
                mock.patch("services.views.payouts.resolve_account", return_value="ADA OKAFOR"), \
                mock.patch("services.views.payouts.create_recipient", return_value="RCP_2"):
            self.assertEqual(self.withdraw().status_code, 201)
        self.bank.refresh_from_db()
        self.assertEqual((self.bank.recipient_code, Withdrawal.objects.get().account_name), ("RCP_2", "ADA OKAFOR"))

    def test_only_staff_approve(self):
        self.withdraw()
        self.assertEqual(self.client.post(f"/api/admin/withdrawals/{Withdrawal.objects.get().id}/approve").status_code, 403)

    def test_approving_sends_the_transfer_once(self):
        self.withdraw()
        withdrawal = Withdrawal.objects.get()
        response, send = self.approve(withdrawal, transfer={"status": "pending", "transfer_code": "TRF_1"})
        self.assertEqual(self.data(response)["status"], "PROCESSING")
        send.assert_called_once_with(Decimal("5000.00"), "RCP_1", withdrawal.reference, "Handwork Expert earnings")
        response, send = self.approve(withdrawal, transfer={"status": "pending"})
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()

    def test_transfer_webhooks_settle_it(self):
        self.withdraw()
        withdrawal = Withdrawal.objects.get()
        self.approve(withdrawal, transfer={"status": "pending", "transfer_code": "TRF_1"})
        self.webhook("transfer.success", withdrawal.reference)
        self.webhook("transfer.success", withdrawal.reference)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, "PAID")
        self.assertEqual(self.balance(), 15000)

    def test_failed_transfers_are_refunded_once(self):
        self.withdraw()
        withdrawal = Withdrawal.objects.get()
        self.approve(withdrawal, transfer={"status": "pending", "transfer_code": "TRF_1"})
        self.webhook("transfer.failed", withdrawal.reference)
        self.webhook("transfer.failed", withdrawal.reference)
        withdrawal.refresh_from_db()
        self.assertEqual((withdrawal.status, self.balance()), ("FAILED", 20000))

    def test_paystack_refusing_the_transfer_refunds_it(self):
        self.withdraw()
        response, _ = self.approve(Withdrawal.objects.get(), error=PaystackError("Insufficient balance"))
        self.assertEqual(self.data(response)["status"], "FAILED")
        self.assertIn("Insufficient balance", self.data(response)["note"])
        self.assertEqual(self.balance(), 20000)

    def test_rejecting_refunds_it(self):
        self.withdraw()
        withdrawal = Withdrawal.objects.get()
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(f"/api/admin/withdrawals/{withdrawal.id}/reject").status_code, 400)
        response = self.client.post(f"/api/admin/withdrawals/{withdrawal.id}/reject", {"reason": "Bank name mismatch."})
        self.assertEqual(self.data(response)["status"], "REJECTED")
        self.assertEqual(self.balance(), 20000)
        counts = self.data(self.client.get("/api/admin/withdrawals/counts"))
        self.assertEqual(counts, {"pending": 0, "processing": 0, "paid": 0, "unsuccessful": 1})

    def test_wallet_summary(self):
        customer = make_user("chi@example.com")
        held = BookArtisan.objects.create(client=customer, artisan=self.artisan, description="x", status="Paid", agreed_amount=10000)
        Transaction.objects.create(artisan=self.artisan, booking=held, gross_amount=10000, platform_fee=1000, vat=0,
                                   net_amount=9000, transaction_type="EARNING")
        self.withdraw()
        wallet = self.data(self.client.get("/api/wallet"))
        self.assertEqual(Decimal(str(wallet["available"])), 15000)
        self.assertEqual(Decimal(str(wallet["on_the_way"])), 9000)
        self.assertEqual(Decimal(str(wallet["total_earned"])), 9000)
        self.assertTrue(wallet["has_open_withdrawal"] and wallet["bank"]["verified"])
        self.assertEqual([item["kind"] for item in wallet["activity"]], ["withdrawal", "earning"])


class AdminOverviewTests(ApiTestCase):
    def setUp(self):
        self.staff = make_user("staff@example.com", is_staff=True)
        self.customer = make_user("chi@example.com")
        self.approved = make_user("ada@example.com", role="ARTISAN", onboardingComplete=True, verification_status="APPROVED")
        self.waiting = make_user("bola@example.com", role="ARTISAN", onboardingComplete=True)
        make_user("eze@example.com", role="ARTISAN")
        held = BookArtisan.objects.create(client=self.customer, artisan=self.approved, description="x", status="Paid", agreed_amount=20000)
        done = BookArtisan.objects.create(client=self.customer, artisan=self.approved, description="y", status="Released", agreed_amount=10000)
        Transaction.objects.create(artisan=self.approved, booking=done, gross_amount=10000, platform_fee=1000, vat=0,
                                   net_amount=9000, transaction_type="EARNING")
        Withdrawal.objects.create(artisan=self.approved, amount=5000, bank_name="GTBank", account_number="1111111111",
                                  account_name="ADA", recipient_code="RCP_1", reference="withdrawal_1")
        self.held = held

    def test_staff_only(self):
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 401)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 403)

    def test_figures_and_lists(self):
        self.client.force_authenticate(self.staff)
        overview = self.data(self.client.get("/api/admin/overview"))
        self.assertEqual(Decimal(str(overview["revenue"]["this_month"])), 1000)
        self.assertEqual((Decimal(str(overview["held"]["amount"])), overview["held"]["bookings"]), (20000, 1))
        self.assertEqual(overview["customers"], {"total": 1, "new_this_month": 1})
        self.assertEqual(overview["artisans"], {"approved": 1, "awaiting_review": 1, "onboarding": 1})
        self.assertEqual((overview["bookings"]["this_month"], overview["bookings"]["active"]), (2, 1))
        self.assertEqual(overview["withdrawals"]["pending"], 1)
        self.assertEqual([a["id"] for a in overview["artisans_awaiting_review"]], [str(self.waiting.id)])
        self.assertEqual(overview["withdrawals_awaiting_approval"][0]["artisan"], "Ada Test")
        self.assertEqual(len(overview["recent_bookings"]), 2)
        self.assertNotIn("staff@example.com", [user["email"] for user in overview["recent_users"]])


class EmailTests(ApiTestCase):
    """Emails go through Resend's HTTP API; a failed send is logged, never raised to the request"""

    @override_settings(EMAIL_BACKEND="main.emails.ResendEmailBackend", RESEND_API_KEY="re_test", EMAIL_IN_BACKGROUND=False)
    @mock.patch("main.emails.requests.post")
    def test_sends_through_resend(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"id": "email_1"})
        send_email("chi@example.com", "Hello", "Plain text", html="<p>Hi</p>")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.args[0], "https://api.resend.com/emails")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer re_test")
        self.assertTrue(post.call_args.kwargs["timeout"])
        self.assertEqual(payload["to"], ["chi@example.com"])
        self.assertEqual((payload["subject"], payload["text"], payload["html"]), ("Hello", "Plain text", "<p>Hi</p>"))
        self.assertEqual(payload["from"], settings.DEFAULT_FROM_EMAIL)

    @override_settings(EMAIL_BACKEND="main.emails.ResendEmailBackend", RESEND_API_KEY="re_test", EMAIL_IN_BACKGROUND=False)
    @mock.patch("main.emails.requests.post")
    def test_refused_send_is_logged_not_raised(self, post):
        post.return_value = mock.Mock(status_code=422, text='{"message": "domain not verified"}')
        with self.assertLogs("main", level="ERROR"):
            send_email("chi@example.com", "Hello", "Plain text")
        with self.assertRaises(ResendError):
            EmailMultiAlternatives("Hello", "Plain text", to=["chi@example.com"], connection=ResendEmailBackend()).send()

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_IN_BACKGROUND=False)
    def test_password_reset_code_is_emailed(self):
        make_user("chi@example.com")
        self.assertEqual(self.client.post("/api/forgot-password/request-otp", {"email": "chi@example.com"}).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(cache.get("password_reset_otp_chi@example.com"), mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_IN_BACKGROUND=False)
    def test_unknown_email_gets_the_same_answer_and_no_email(self):
        self.assertEqual(self.client.post("/api/forgot-password/request-otp", {"email": "nobody@example.com"}).status_code, 200)
        self.assertEqual(mail.outbox, [])


class DeleteAccountTests(ApiTestCase):
    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN", onboardingComplete=True, verification_status="APPROVED")

    def delete_as(self, user):
        self.client.force_authenticate(user)
        return self.client.delete("/api/delete-account")

    def test_needs_a_signed_in_user(self):
        self.assertEqual(self.client.delete("/api/delete-account").status_code, 401)

    def test_deactivates_the_account_and_hides_the_artisan(self):
        self.assertEqual(self.delete_as(self.artisan).status_code, 200)
        self.artisan.refresh_from_db()
        self.assertFalse(self.artisan.is_active)

        self.client.force_authenticate(None)
        self.assertEqual(self.data(self.client.get("/api/artisan")), [])
        self.assertEqual(self.client.get(f"/api/artisan/{self.artisan.id}").status_code, 404)

    def test_refused_while_a_booking_is_open(self):
        booking = BookArtisan.objects.create(client=self.customer, artisan=self.artisan, description="x", status="Paid")
        for user in (self.customer, self.artisan):
            self.assertEqual(self.delete_as(user).status_code, 400)
            user.refresh_from_db()
            self.assertTrue(user.is_active)

        booking.status = "Released"
        booking.save()
        self.assertEqual(self.delete_as(self.customer).status_code, 200)

    def test_refused_while_the_artisan_has_money_with_us(self):
        self.artisan.wallet_balance = 500
        self.artisan.save()
        self.assertEqual(self.delete_as(self.artisan).status_code, 400)

        self.artisan.wallet_balance = 0
        self.artisan.save()
        Withdrawal.objects.create(artisan=self.artisan, amount=5000, bank_name="GTBank", account_number="1111111111",
                                  account_name="ADA", recipient_code="RCP_1", reference="withdrawal_1")
        self.assertEqual(self.delete_as(self.artisan).status_code, 400)


class AdminUserTests(ApiTestCase):
    def setUp(self):
        self.staff = make_user("staff@example.com", is_staff=True)
        self.customer = make_user("chi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN", onboardingComplete=True, verification_status="APPROVED")
        self.gone = make_user("gone@example.com", is_active=False)
        BookArtisan.objects.create(client=self.customer, artisan=self.artisan, description="x", status="Paid")
        BookArtisan.objects.create(client=self.customer, artisan=self.artisan, description="y", status="Released")

    def test_staff_only(self):
        self.assertEqual(self.client.get("/api/admin/users").status_code, 401)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.assertEqual(self.client.post(f"/api/admin/users/{self.artisan.id}/deactivate", {"reason": "Because"}).status_code, 403)

    def test_lists_by_type_with_booking_counts_and_no_id_numbers(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.data(self.client.get("/api/admin/users/counts")), {"customers": 1, "artisans": 1, "staff": 1, "deactivated": 1})
        customers = self.data(self.client.get("/api/admin/users?status=customers"))
        self.assertEqual([user["email"] for user in customers], ["chi@example.com"])
        self.assertEqual((customers[0]["bookings"], customers[0]["open_bookings"]), (2, 1))
        self.assertEqual(self.data(self.client.get("/api/admin/users?status=artisans"))[0]["open_bookings"], 1)
        self.assertFalse({"nin", "bvn", "document", "password"} & set(customers[0]))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_IN_BACKGROUND=False)
    def test_deactivate_and_reactivate(self):
        self.client.force_authenticate(self.staff)
        url = f"/api/admin/users/{self.artisan.id}"
        self.assertEqual(self.client.post(f"{url}/deactivate", {}).status_code, 400)

        self.assertEqual(self.client.post(f"{url}/deactivate", {"reason": "Fake documents"}).status_code, 200)
        self.artisan.refresh_from_db()
        self.assertFalse(self.artisan.is_active)
        self.assertIn("Fake documents", mail.outbox[-1].body)
        self.assertEqual(self.data(self.client.get("/api/artisan")), [])
        self.assertEqual(self.client.post(f"{url}/deactivate", {"reason": "Again"}).status_code, 400)

        self.assertEqual(self.client.post(f"{url}/reactivate").status_code, 200)
        self.artisan.refresh_from_db()
        self.assertTrue(self.artisan.is_active)
        self.assertEqual(len(self.data(self.client.get("/api/artisan"))), 1)

    def test_staff_accounts_cant_be_deactivated_here(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(f"/api/admin/users/{self.staff.id}/deactivate", {"reason": "Oops"}).status_code, 400)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)


class AdminServiceTests(ApiTestCase):
    def setUp(self):
        self.staff = make_user("staff@example.com", is_staff=True)
        self.customer = make_user("chi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN")
        self.home = Category.objects.create(name="Home", description="Home")
        self.plumbing = Services.objects.create(name="Plumbing", description="Pipes")
        self.home.service.add(self.plumbing)
        self.client.force_authenticate(self.staff)

    def test_staff_only(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/services").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/services", {"name": "Spam", "description": "x"}).status_code, 403)

    def test_create_edit_and_usage_counts(self):
        response = self.client.post("/api/admin/services", {"name": "Tiling", "description": "Floors", "categories": [self.home.id]}, format="json")
        self.assertEqual(response.status_code, 201)
        service_id = self.data(response)["id"]
        self.assertEqual(self.data(response)["categories"], [self.home.id])
        self.assertTrue(self.home.service.filter(pk=service_id).exists())

        duplicate = self.client.post("/api/admin/services", {"name": "tiling", "description": "Again"}, format="json")
        self.assertEqual(duplicate.status_code, 400)

        other = Category.objects.create(name="Other", description="Other")
        edited = self.client.patch(f"/api/admin/services/{service_id}", {"name": "Floor tiling", "categories": [other.id]}, format="json")
        self.assertEqual(edited.status_code, 200)
        self.assertFalse(self.home.service.filter(pk=service_id).exists())
        self.assertTrue(other.service.filter(pk=service_id).exists())

    def test_hidden_services_leave_the_public_lists(self):
        hidden = self.client.patch(f"/api/admin/services/{self.plumbing.id}", {"active": False}, format="json")
        self.assertEqual(hidden.status_code, 200)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/get_services").status_code, 404)
        self.assertEqual(self.data(self.client.get("/api/services")), [])
        self.assertEqual(self.data(self.client.get("/api/category"))[0]["service"], [])
        self.client.force_authenticate(self.staff)
        self.assertEqual(len(self.data(self.client.get("/api/admin/services"))), 1)

    def test_a_service_in_use_cant_be_deleted(self):
        skill = Skill.objects.create(artisan=self.artisan, description="x", base_charge=1, cap_charge=2, weekdays_and_weekends=True)
        skill.services.set([self.plumbing])
        self.assertEqual(self.client.delete(f"/api/admin/services/{self.plumbing.id}").status_code, 400)

        skill.services.clear()
        self.assertEqual(self.client.delete(f"/api/admin/services/{self.plumbing.id}").status_code, 204)
        self.assertFalse(Services.objects.exists())


def small_image(name="work.png"):
    buffer = BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class SkillUpdateTests(ApiTestCase):
    def setUp(self):
        self.artisan = make_user("ada@example.com", role="ARTISAN", verification_status="APPROVED")
        self.plumbing = Services.objects.create(name="Plumbing", description="Pipes")
        self.painting = Services.objects.create(name="Painting", description="Walls")
        self.skill = Skill.objects.create(artisan=self.artisan, description="Fixing leaks", base_charge=1000, cap_charge=5000)
        self.skill.services.set([self.plumbing])
        self.url = f"/api/skill/{self.skill.id}"
        self.client.force_authenticate(self.artisan)

    def test_only_the_owner_can_edit(self):
        self.client.force_authenticate(make_user("bola@example.com", role="ARTISAN"))
        self.assertEqual(self.client.patch(self.url, {"description": "Hijacked"}, format="multipart").status_code, 404)
        self.client.force_authenticate(make_user("chi@example.com"))
        self.assertEqual(self.client.patch(self.url, {"description": "Hijacked"}, format="multipart").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.patch(self.url, {"description": "Hijacked"}, format="multipart").status_code, 401)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.description, "Fixing leaks")

    def test_edit_keeps_what_isnt_sent_and_cant_change_the_owner(self):
        other = make_user("bola@example.com", role="ARTISAN")
        response = self.client.patch(self.url, {"description": "Twenty years of pipes", "artisan": other.id}, format="multipart")
        self.assertEqual(response.status_code, 200, response.content)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.description, "Twenty years of pipes")
        self.assertEqual(self.skill.artisan, self.artisan)
        self.assertEqual(list(self.skill.services.all()), [self.plumbing])
        self.assertEqual(self.skill.base_charge, 1000)

    def test_services_prices_and_work_days_can_change(self):
        response = self.client.patch(self.url, {
            "services": "[%d, %d]" % (self.plumbing.id, self.painting.id),
            "base_charge": "2000",
            "cap_charge": "9000",
            "weekdays_only": "true",
            "weekends_only": "false",
            "weekdays_and_weekends": "false",
        }, format="multipart")
        self.assertEqual(response.status_code, 200, response.content)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.services.count(), 2)
        self.assertEqual(self.skill.cap_charge, 9000)
        self.assertTrue(self.skill.weekdays_only)
        self.assertFalse(self.skill.weekdays_and_weekends)

    def test_refuses_no_services_and_a_cap_below_the_base(self):
        self.assertEqual(self.client.patch(self.url, {"services": "[]"}, format="multipart").status_code, 400)
        self.assertEqual(self.client.patch(self.url, {"cap_charge": "500"}, format="multipart").status_code, 400)

    def test_a_hidden_service_can_be_kept_but_not_newly_picked(self):
        Services.objects.filter(pk=self.plumbing.pk).update(active=False)
        kept = self.client.patch(self.url, {"services": f"[{self.plumbing.id}, {self.painting.id}]"}, format="multipart")
        self.assertEqual(kept.status_code, 200, kept.content)

        self.skill.services.set([self.painting])
        added = self.client.patch(self.url, {"services": f"[{self.plumbing.id}]"}, format="multipart")
        self.assertEqual(added.status_code, 400)

    def test_edit_after_a_rejection_goes_back_for_review_but_an_approved_artisan_stays_approved(self):
        self.client.patch(self.url, {"description": "Still approved"}, format="multipart")
        self.artisan.refresh_from_db()
        self.assertEqual(self.artisan.verification_status, "APPROVED")

        User.objects.filter(pk=self.artisan.pk).update(verification_status="REJECTED", verification_note="Blurry photos")
        self.client.patch(self.url, {"description": "Better now"}, format="multipart")
        self.artisan.refresh_from_db()
        self.assertEqual(self.artisan.verification_status, "PENDING")
        self.assertEqual(self.artisan.verification_note, "")

    def test_photos_can_be_replaced_and_removed(self):
        Skill.objects.filter(pk=self.skill.pk).update(image1="skill_images/old1.png", image2="skill_images/old2.png")
        with (
            mock.patch.object(PublicSpacesStorage, "save", return_value="skill_images/new.png") as saved,
            mock.patch.object(PublicSpacesStorage, "delete") as deleted,
        ):
            response = self.client.patch(self.url, {"image1": small_image(), "image2": ""}, format="multipart")
        self.assertEqual(response.status_code, 200, response.content)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.image1.name, "skill_images/new.png")
        self.assertFalse(self.skill.image2)
        saved.assert_called_once()
        self.assertEqual({call.args[0] for call in deleted.call_args_list}, {"skill_images/old1.png", "skill_images/old2.png"})


class EmailGreetingTests(ApiTestCase):
    def test_uses_the_first_name_when_there_is_one(self):
        self.assertEqual(greeting(User(first_name="Ada")), "Hi Ada,")

    def test_leaves_it_out_when_there_isnt(self):
        self.assertEqual(greeting(User(first_name="")), "Hi,")
        self.assertEqual(greeting(User(first_name="  ")), "Hi,")
