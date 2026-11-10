from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from main.models import Notification, Rating
from main.tests import ApiTestCase, make_user
from services.models.job_models import BookArtisan
from services.models.services_models import Services


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReviewTests(ApiTestCase):
    def setUp(self):
        self.customer = make_user("chi@example.com")
        self.other_customer = make_user("femi@example.com")
        self.artisan = make_user("ada@example.com", role="ARTISAN", verification_status="APPROVED")
        self.other_artisan = make_user("bayo@example.com", role="ARTISAN", verification_status="APPROVED")
        self.plumbing = Services.objects.create(name="Plumbing", description="Pipes")
        self.booking = self.make_booking("Released")

    def make_booking(self, status, customer=None):
        booking = BookArtisan.objects.create(client=customer or self.customer, artisan=self.artisan,
                                             description="Leaking sink", status=status)
        booking.services.set([self.plumbing])
        return booking

    def review(self, booking=None, user=None, **overrides):
        self.client.force_authenticate(user or self.customer)
        return self.client.post("/api/artisan_rating", {
            "booking": (booking or self.booking).id, "star": 4, "message": "Fixed it fast.", **overrides,
        }, format="json")

    def test_a_customer_reviews_a_released_job(self):
        response = self.review(artisan=str(self.other_artisan.id))
        self.assertEqual(response.status_code, 201, response.content)
        review = self.data(response)
        # The artisan comes from the booking, whatever the body says
        self.assertEqual(review["artisan"], "Ada Test")
        self.assertEqual((review["client"], review["verified"], review["services"]), ("Chi T.", True, ["Plumbing"]))
        self.assertTrue(Notification.objects.filter(recipient=self.artisan, message__contains="4-star review").exists())

    def test_only_released_jobs_can_be_reviewed(self):
        for status in ("Accepted", "Paid", "In Progress", "Completed", "Cancelled", "Disputed", "Refunded"):
            self.assertEqual(self.review(self.make_booking(status)).status_code, 400, status)

    def test_one_review_per_job(self):
        self.review()
        self.assertEqual(self.review().status_code, 400)

    def test_customers_only_review_their_own_jobs(self):
        self.assertEqual(self.review(user=self.other_customer).status_code, 400)

    def test_a_review_needs_a_job(self):
        self.client.force_authenticate(self.customer)
        response = self.client.post("/api/artisan_rating", {"artisan": str(self.artisan.id), "star": 5}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("booking", response.json()["data"])

    def test_the_written_part_is_optional(self):
        self.assertEqual(self.review(message="").status_code, 201)

    def test_the_artisan_replies_and_the_customer_is_told(self):
        review_id = self.data(self.review())["id"]
        self.client.force_authenticate(self.other_artisan)
        self.assertEqual(self.client.post(f"/api/artisan_rating/{review_id}/reply", {"reply": "Hi"}).status_code, 403)

        self.client.force_authenticate(self.artisan)
        response = self.client.post(f"/api/artisan_rating/{review_id}/reply", {"reply": "Thanks, Chi!"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.data(response)["reply"], "Thanks, Chi!")
        self.assertTrue(Notification.objects.filter(recipient=self.customer, message__contains="replied").exists())

    def test_a_review_cant_change_after_a_reply_or_two_weeks(self):
        review_id = self.data(self.review())["id"]
        Rating.objects.filter(pk=review_id).update(created_at=timezone.now() - timedelta(days=15))
        self.assertEqual(self.client.patch(f"/api/artisan_rating/{review_id}", {"star": 1}).status_code, 400)

        Rating.objects.filter(pk=review_id).update(created_at=timezone.now(), reply="Thanks")
        self.assertEqual(self.client.patch(f"/api/artisan_rating/{review_id}", {"star": 1}).status_code, 400)

    def test_a_review_stays_with_its_job(self):
        review_id = self.data(self.review())["id"]
        other = self.make_booking("Released")
        response = self.client.patch(f"/api/artisan_rating/{review_id}", {"booking": other.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_hidden_reviews_drop_out_everywhere(self):
        review_id = self.data(self.review(star=1))["id"]
        self.data(self.review(self.make_booking("Released"), star=5))
        Rating.objects.filter(pk=review_id).update(hidden_at=timezone.now(), hidden_reason="Fake")

        self.client.force_authenticate(None)
        listed = self.data(self.client.get(f"/api/artisan_rating?artisan={self.artisan.id}"))
        self.assertEqual([item["star"] for item in listed], [5])
        profile = self.data(self.client.get(f"/api/artisan/{self.artisan.id}"))
        self.assertEqual(profile["review_summary"], {"average": 5.0, "count": 1})

    def test_unverified_old_ratings_dont_count_in_the_summary(self):
        Rating.objects.create(client=self.customer, artisan=self.artisan, star=1, message="Old")
        self.review(star=5)
        profile = self.data(self.client.get(f"/api/artisan/{self.artisan.id}"))
        self.assertEqual(profile["review_summary"], {"average": 5.0, "count": 1})

    def test_bookings_show_their_review(self):
        review_id = self.data(self.review(star=5))["id"]
        bookings = self.data(self.client.get("/api/my_bookings"))
        self.assertEqual(bookings[0]["review"], {"id": review_id, "star": 5})

    def test_listing_by_artisan(self):
        self.review()
        self.client.force_authenticate(None)
        self.assertEqual(len(self.data(self.client.get(f"/api/artisan_rating?artisan={self.artisan.id}"))), 1)
        self.assertEqual(self.data(self.client.get(f"/api/artisan_rating?artisan={self.other_artisan.id}")), [])
        self.assertEqual(self.data(self.client.get("/api/artisan_rating?artisan=not-a-uuid")), [])
