from rest_framework.test import APITestCase

from main.models import BankDetails, Rating, User

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
        self.artisan = make_user("ada@example.com", role="ARTISAN", nin="12345678901", bvn="10987654321")
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

    def test_logs_need_staff(self):
        self.assertEqual(self.client.get("/api/logs/").status_code, 401)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/logs/").status_code, 403)
