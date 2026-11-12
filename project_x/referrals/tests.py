from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.test import override_settings

from main.models import User
from main.tests import ApiTestCase, make_user
from referrals.models import Referral, ReferralCode, ReferralReward
from services.models.job_models import BookArtisan, Transaction
from services.views.job_process import record_payment, release_booking

PAYSTACK_OK = {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/x", "access_code": "ac_1", "reference": "r"}}


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], EMAIL_IN_BACKGROUND=False)
class ReferralTests(ApiTestCase):
    def setUp(self):
        self.chi = make_user("chi@example.com")  # a customer who invites friends
        self.ada = make_user("ada@example.com", role="ARTISAN", verification_status="APPROVED")  # an artisan who invites artisans
        self.other_artisan = make_user("bayo@example.com", role="ARTISAN", verification_status="APPROVED")

    def code_for(self, user):
        return ReferralCode.for_user(user).code

    def sign_up(self, kind, email, code, contact):
        return self.client.post(f"/api/{kind}", {
            "first_name": email.split("@")[0].title(), "last_name": "New", "email": email, "contact": contact,
            "password": "Secret#123", "referral_code": code,
        })

    def booking(self, client, artisan, amount=20000, status="Completed"):
        return BookArtisan.objects.create(client=client, artisan=artisan, description="Job", budget=amount,
                                          agreed_amount=amount, status=status)

    def release(self, booking):
        with transaction.atomic():
            release_booking(booking)

    def test_my_code_and_who_signed_up(self):
        self.client.force_authenticate(self.chi)
        data = self.data(self.client.get("/api/referrals/me"))
        self.assertEqual(len(data["code"]), 7)
        self.assertEqual((data["referrals"], data["available"], data["percent"]), ([], 0, "5"))
        # The same code every time
        self.assertEqual(self.data(self.client.get("/api/referrals/me"))["code"], data["code"])

    def test_only_same_role_sign_ups_count(self):
        code = self.code_for(self.chi)
        self.assertEqual(self.sign_up("user", "femi@example.com", code.lower(), "+2348010000001").status_code, 201)
        self.assertEqual(self.sign_up("artisan", "tunde@example.com", code, "+2348010000002").status_code, 201)
        self.assertEqual(self.sign_up("user", "kemi@example.com", "NOPE123", "+2348010000003").status_code, 201)
        referrals = Referral.objects.all()
        self.assertEqual([(r.referrer.email, r.referred.email, r.role) for r in referrals], [("chi@example.com", "femi@example.com", "USER")])

    def test_a_customer_earns_5_percent_off_after_their_friend_s_first_job(self):
        self.sign_up("user", "femi@example.com", self.code_for(self.chi), "+2348010000001")
        femi = User.objects.get(email="femi@example.com")
        # Nothing until the friend's first job is paid out
        self.assertFalse(ReferralReward.objects.exists())
        self.release(self.booking(femi, self.other_artisan))
        self.release(self.booking(femi, self.other_artisan))  # a second job earns nothing more
        reward = ReferralReward.objects.get()
        self.assertEqual((reward.owner, reward.kind, reward.status), (self.chi, "CUSTOMER_DISCOUNT", "AVAILABLE"))

        # Chi's next booking: she pays 95%, the artisan still gets 90% of the full price, our fee covers the 5%
        booking = self.booking(self.chi, self.other_artisan, amount=20000, status="Accepted")
        self.client.force_authenticate(self.chi)
        listed = next(item for item in self.data(self.client.get("/api/my_bookings")) if item["id"] == booking.id)
        self.assertEqual(Decimal(listed["amount_due"]), Decimal("19000"))
        with mock.patch("services.views.job_process.paystack.transaction.initialize", return_value=PAYSTACK_OK) as start:
            self.assertEqual(self.client.post(f"/api/book/{booking.id}/pay").status_code, 200)
        self.assertEqual(start.call_args.kwargs["amount"], 1_900_000)
        reward.refresh_from_db()
        self.assertEqual(reward.status, "RESERVED")

        record_payment(f"full_{booking.id}_1", 1_900_000)
        booking.refresh_from_db()
        reward.refresh_from_db()
        self.assertEqual((booking.status, booking.referral_discount, reward.status), ("Paid", Decimal("1000.00"), "USED"))
        booking.status = "Completed"
        booking.save()
        self.release(booking)
        earning = Transaction.objects.get(booking=booking)
        self.assertEqual((earning.net_amount, earning.platform_fee), (Decimal("18000.00"), Decimal("1000.00")))

    def test_cancelling_before_paying_gives_the_discount_back(self):
        self.sign_up("user", "femi@example.com", self.code_for(self.chi), "+2348010000001")
        self.release(self.booking(User.objects.get(email="femi@example.com"), self.other_artisan))
        booking = self.booking(self.chi, self.other_artisan, status="Accepted")
        self.client.force_authenticate(self.chi)
        with mock.patch("services.views.job_process.paystack.transaction.initialize", return_value=PAYSTACK_OK):
            self.client.post(f"/api/book/{booking.id}/pay")
        self.assertEqual(self.client.post(f"/api/book/{booking.id}/cancel", {"reason": ""}, format="json").status_code, 200)
        self.assertEqual(ReferralReward.objects.get().status, "AVAILABLE")

    def test_an_artisan_keeps_95_percent_of_their_next_job(self):
        self.sign_up("artisan", "tunde@example.com", self.code_for(self.ada), "+2348010000002")
        tunde = User.objects.get(email="tunde@example.com")
        self.release(self.booking(self.chi, tunde))
        reward = ReferralReward.objects.get()
        self.assertEqual((reward.owner, reward.kind), (self.ada, "ARTISAN_FEE_CUT"))

        booking = self.booking(self.chi, self.ada, amount=20000)
        self.release(booking)
        earning = Transaction.objects.get(booking=booking)
        self.assertEqual((earning.net_amount, earning.platform_fee), (Decimal("19000.00"), Decimal("1000.00")))
        reward.refresh_from_db()
        self.assertEqual((reward.status, reward.booking_id), ("USED", booking.id))
        # Only that one job
        later = self.booking(self.chi, self.ada, amount=20000)
        self.release(later)
        self.assertEqual(Transaction.objects.get(booking=later).net_amount, Decimal("18000.00"))
