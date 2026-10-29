from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from services.models.job_models import BookArtisan
from services.views.job_process import RELEASE_AFTER_HOURS, release_booking


class Command(BaseCommand):
    help = (
        f"Pays artisans for jobs marked done more than {RELEASE_AFTER_HOURS} hours ago that the customer "
        "hasn't confirmed or disputed. Run it on a schedule (e.g. hourly, as a Railway cron job)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the bookings without paying anyone")

    def handle(self, *args, dry_run=False, **options):
        cutoff = timezone.now() - timedelta(hours=RELEASE_AFTER_HOURS)
        # Old deposit bookings are paid out when the customer pays the balance instead
        due = BookArtisan.objects.filter(status="Completed", deposit_paid_at__isnull=True, completed_at__lte=cutoff)
        released = 0
        for booking_id in due.values_list("id", flat=True):
            if dry_run:
                self.stdout.write(f"Would release booking {booking_id}")
                continue
            with transaction.atomic():
                booking = BookArtisan.objects.select_for_update(of=("self",)).select_related("client", "artisan").get(id=booking_id)
                if booking.status != "Completed":
                    continue
                release_booking(booking, automatic=True)
                released += 1
        self.stdout.write(self.style.SUCCESS(f"Released {released} booking(s)."))
