from rest_framework.test import APITestCase

from rest_framework import serializers as drf_serializers

from main.models import BankDetails, Notification, Rating, Skill, User
from services.models.services_models import Services
from services.serializers.job_serializers import ArtisanBookingSerializers

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

    def test_create_always_belongs_to_the_signed_in_artisan(self):
        self.client.force_authenticate(self.artisan)
        response = self.client.post("/api/bank_details", {
            "name": "My Bank", "account_number": "1111111111", "account_name": "Ada Test",
            "artisan": str(self.other_artisan.id),
        })
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(BankDetails.objects.filter(artisan=self.artisan, name="My Bank").exists())


class RatingAndLogsTests(ApiTestCase):
    def setUp(self):
        self.artisan = make_user("ada@example.com", role="ARTISAN")
        self.customer = make_user("chi@example.com")
        self.other_customer = make_user("dee@example.com")

    def test_ratings_are_written_as_the_signed_in_customer(self):
        payload = {"artisan": str(self.artisan.id), "star": 5, "message": "Great", "client": str(self.other_customer.id)}
        self.assertEqual(self.client.post("/api/artisan_rating", payload).status_code, 401)

        self.client.force_authenticate(self.artisan)
        self.assertEqual(self.client.post("/api/artisan_rating", payload).status_code, 403)

        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post("/api/artisan_rating", payload).status_code, 201)
        self.assertEqual(Rating.objects.get().client, self.customer)

    def test_only_the_author_can_change_a_rating(self):
        rating = Rating.objects.create(client=self.customer, artisan=self.artisan, star=4, message="Good")
        self.client.force_authenticate(self.other_customer)
        self.assertEqual(self.client.patch(f"/api/artisan_rating/{rating.id}", {"star": 1}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/artisan_rating/{rating.id}").status_code, 403)

        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.patch(f"/api/artisan_rating/{rating.id}", {"star": 5}).status_code, 200)

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


class BookingTests(ApiTestCase):
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
            "scheduled_date": "2026-11-02",
            "scheduled_time": "10:00",
            **overrides,
        }, format="json")

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
