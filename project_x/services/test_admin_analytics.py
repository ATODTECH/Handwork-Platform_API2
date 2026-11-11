from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from main.models import Rating
from main.tests import make_user
from services.models.job_models import BookArtisan, Transaction
from services.models.services_models import Category
from services.test_marketplace import MarketplaceTestCase
from services.views.job_process import release_booking


class AdminAnalyticsTests(MarketplaceTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_user("staff@example.com", is_staff=True)
        home = Category.objects.create(name="Home")
        home.service.set([self.plumbing, self.tiling])

    def released_job(self, amount=20000, artisan=None):
        booking = BookArtisan.objects.create(client=self.customer, artisan=artisan or self.plumber, description="Sink",
                                             budget=amount, agreed_amount=amount, status="Completed")
        booking.services.set([self.plumbing])
        with transaction.atomic():
            release_booking(booking)
        return booking

    def analytics(self, query=""):
        self.client.force_authenticate(self.staff)
        response = self.client.get(f"/api/admin/analytics{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return self.data(response)

    def test_staff_only(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get("/api/admin/analytics").status_code, 403)

    def test_months_are_clamped(self):
        self.assertEqual(len(self.analytics()["months"]), 6)
        self.assertEqual(len(self.analytics("?months=12")["months"]), 12)
        self.assertEqual(len(self.analytics("?months=1")["months"]), 3)
        self.assertEqual(len(self.analytics("?months=99")["months"]), 24)
        self.assertEqual(len(self.analytics("?months=abc")["months"]), 6)

    def test_money_comes_from_released_jobs(self):
        self.released_job(20000)
        self.released_job(30000, artisan=self.plumber2)
        pending = BookArtisan.objects.create(client=self.customer, artisan=self.plumber, description="Pending", budget=9000)
        pending.services.set([self.tiling])
        data = self.analytics()
        totals = data["totals"]
        self.assertEqual(Decimal(totals["fees"]), Decimal("5000"))
        self.assertEqual(Decimal(totals["job_value"]), Decimal("50000"))
        self.assertEqual(Decimal(totals["paid_to_artisans"]), Decimal("45000"))
        self.assertEqual((totals["bookings"], totals["completed"]), (3, 2))
        # This month is the last in each series
        self.assertEqual(Decimal(data["series"]["fees"][-1]), Decimal("5000"))
        self.assertEqual(data["series"]["bookings"][-1], 3)
        self.assertEqual(sum(data["series"]["bookings"][:-1]), 0)
        self.assertEqual([row["name"] for row in data["top_artisans"]], ["Bayo Test", "Ada Test"])
        self.assertEqual(data["top_categories"][0]["name"], "Home")
        self.assertEqual(data["top_categories"][0]["bookings"], 3)

    def test_last_window_is_kept_apart(self):
        self.released_job(20000)
        old = timezone.now() - timedelta(days=200)
        Transaction.objects.update(created_at=old)
        BookArtisan.objects.update(created_at=old)
        data = self.analytics()
        self.assertEqual(Decimal(data["totals"]["fees"]), Decimal("0"))
        self.assertEqual(Decimal(data["previous"]["fees"]), Decimal("2000"))
        self.assertEqual(data["previous"]["bookings"], 1)

    def test_request_funnel(self):
        job_request = self.published()
        self.published(title="Blocked shower drain")
        self.assertEqual(self.offer(job_request).status_code, 201)
        self.assertEqual(self.offer(job_request, self.plumber2).status_code, 201)
        funnel = self.analytics()["funnel"]
        self.assertEqual((funnel["posted"], funnel["with_offer"], funnel["accepted"]), (2, 1, 0))
        self.assertEqual(funnel["average_offers"], 1.0)
        self.assertIsNotNone(funnel["median_hours_to_first_offer"])

    def test_verified_reviews_give_the_average(self):
        booking = self.released_job()
        Rating.objects.create(client=self.customer, artisan=self.plumber, booking=booking, star=4)
        Rating.objects.create(client=self.customer, artisan=self.plumber, star=1)  # old rating, not tied to a job
        totals = self.analytics()["totals"]
        self.assertEqual((totals["reviews"], totals["average_rating"]), (1, 4.0))
