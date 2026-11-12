from decimal import Decimal

from django.core import mail
from django.test import override_settings

from main.models import User
from main.tests import ApiTestCase, make_user
from services.models.job_models import BookArtisan, Withdrawal


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], EMAIL_IN_BACKGROUND=False)
class StaffSettingsTests(ApiTestCase):
    def setUp(self):
        self.admin = make_user("boss@example.com", is_staff=True, is_superuser=True)
        self.support = make_user("help@example.com", is_staff=True)
        self.customer = make_user("chi@example.com")

    def as_user(self, user):
        self.client.force_authenticate(user)

    def add_staff(self, **overrides):
        return self.client.post("/api/admin/staff", {
            "first_name": "Tola", "last_name": "New", "email": "tola@example.com", "contact": "+2348000000001",
            "role": "SUPPORT", **overrides,
        }, format="json")

    def test_me_shows_the_role(self):
        self.as_user(self.support)
        me = self.data(self.client.get("/api/admin/me"))
        self.assertEqual((me["email"], me["role"]), ("help@example.com", "SUPPORT"))
        self.as_user(self.admin)
        self.assertEqual(self.data(self.client.get("/api/admin/me"))["role"], "ADMIN")
        self.as_user(self.customer)
        self.assertEqual(self.client.get("/api/admin/me").status_code, 403)

    def test_staff_edit_their_profile_but_not_their_email(self):
        self.as_user(self.support)
        response = self.client.patch("/api/admin/me", {"first_name": " Ngozi ", "email": "x@example.com"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.support.refresh_from_db()
        self.assertEqual((self.support.first_name, self.support.email), ("Ngozi", "help@example.com"))

    def test_password_change_needs_the_current_one(self):
        self.as_user(self.support)
        wrong = self.client.post("/api/admin/me/password", {"current_password": "nope", "new_password": "Brand#New2026"})
        self.assertEqual(wrong.status_code, 400)
        right = self.client.post("/api/admin/me/password", {"current_password": "Secret#123", "new_password": "Brand#New2026"})
        self.assertEqual(right.status_code, 200, right.content)
        self.support.refresh_from_db()
        self.assertTrue(self.support.check_password("Brand#New2026"))

    def test_admins_add_staff_who_get_an_email(self):
        self.as_user(self.support)
        self.assertEqual(self.add_staff().status_code, 403)
        self.as_user(self.admin)
        response = self.add_staff()
        self.assertEqual(response.status_code, 201, response.content)
        body = self.data(response)
        new = User.objects.get(email="tola@example.com")
        self.assertTrue(new.is_staff and not new.is_superuser)
        self.assertTrue(new.check_password(body["temporary_password"]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(body["temporary_password"], mail.outbox[0].body)
        self.assertEqual(self.add_staff(contact="+2348000000002").status_code, 400)  # same email

    def test_roles_and_the_last_admin(self):
        self.as_user(self.admin)
        response = self.client.post(f"/api/admin/staff/{self.support.pk}/role", {"role": "ADMIN"}, format="json")
        self.assertEqual(self.data(response)["role"], "ADMIN")
        # Nobody changes their own access
        self.assertEqual(self.client.post(f"/api/admin/staff/{self.admin.pk}/deactivate").status_code, 400)
        # With two admins, one can step the other down
        self.support.refresh_from_db()
        self.as_user(self.support)
        self.assertEqual(self.client.post(f"/api/admin/staff/{self.admin.pk}/role", {"role": "SUPPORT"}).status_code, 200)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_superuser)
        # ...who then can't manage staff any more
        self.as_user(self.admin)
        self.assertEqual(self.client.post(f"/api/admin/staff/{self.support.pk}/deactivate").status_code, 403)

    def test_deactivated_staff_lose_access(self):
        self.as_user(self.admin)
        self.assertEqual(self.client.post(f"/api/admin/staff/{self.support.pk}/deactivate").status_code, 200)
        self.support.refresh_from_db()
        self.assertFalse(self.support.is_active)
        self.assertEqual(self.client.post(f"/api/admin/staff/{self.customer.pk}/deactivate").status_code, 404)
        self.assertEqual(self.client.post(f"/api/admin/staff/{self.support.pk}/reactivate").status_code, 200)

    def test_support_staff_cant_move_money(self):
        artisan = make_user("ada@example.com", role="ARTISAN")
        withdrawal = Withdrawal.objects.create(artisan=artisan, amount=Decimal("5000"), bank_name="GTBank",
                                               account_number="0123456789", account_name="Ada", recipient_code="R",
                                               reference="WD_1")
        booking = BookArtisan.objects.create(client=self.customer, artisan=artisan, description="Sink", budget=9000,
                                             agreed_amount=9000, status="Disputed")
        self.as_user(self.support)
        self.assertEqual(self.client.get("/api/admin/withdrawals").status_code, 200)
        self.assertEqual(self.client.post(f"/api/admin/withdrawals/{withdrawal.pk}/reject", {"reason": "Wrong bank details"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/admin/bookings/{booking.pk}/resolve", {"outcome": "release", "note": "Checked with both"}).status_code, 403)
        self.as_user(self.admin)
        self.assertEqual(self.client.post(f"/api/admin/withdrawals/{withdrawal.pk}/reject", {"reason": "Wrong bank details"}).status_code, 200)

    def test_with_no_admins_yet_every_staff_member_is_one(self):
        User.objects.filter(pk=self.admin.pk).update(is_superuser=False)
        self.as_user(self.support)
        self.assertEqual(self.data(self.client.get("/api/admin/me"))["role"], "ADMIN")
        # Adding someone makes the person doing it the first real admin
        self.assertEqual(self.add_staff().status_code, 201)
        self.support.refresh_from_db()
        self.assertTrue(self.support.is_superuser)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LastSignInTests(ApiTestCase):
    def test_signing_in_records_the_time(self):
        user = make_user("help@example.com", is_staff=True)
        self.assertIsNone(user.last_login)
        response = self.client.post("/signin/client", {"email": "help@example.com", "password": "Secret#123"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)
