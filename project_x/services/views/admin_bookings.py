"""
Staff view of every booking, and settling problems customers report. A reported problem (Disputed) holds
the payment until staff either pay the artisan (Released) or refund the customer through Paystack (Refunded).
"""
import logging

from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.utils import timezone
from rest_framework import response, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser

from main.models import Notification
from main.paystack import PaystackError, refund_charge
from services.models.job_models import BookArtisan
from services.serializers.job_serializers import AdminBookingSerializer, ResolveDisputeSerializer
from services.views.job_process import release_booking

logger = logging.getLogger('services')

BOOKING_FILTERS = {
    "disputed": Q(status="Disputed"),
    "requested": Q(status="Pending"),
    "awaiting_payment": Q(status="Accepted"),
    "in_progress": Q(status__in=("Paid", "Deposit Paid", "In Progress", "Completed")),
    "done": Q(status__in=("Released", "Paid in Full", "Refunded")),
    "closed": Q(status__in=("Declined", "Cancelled")),
}


class AdminBookingViewSet(viewsets.ReadOnlyModelViewSet):
    """Every booking by stage, and `resolve` for reported problems"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminBookingSerializer

    def get_queryset(self):
        queryset = (BookArtisan.objects.select_related("client", "artisan", "resolved_by")
                    .prefetch_related("services").order_by("-created_at"))
        status_filter = BOOKING_FILTERS.get(self.request.query_params.get("status", ""))
        return queryset.filter(status_filter) if status_filter is not None else queryset

    @action(detail=False, methods=["get"])
    def counts(self, request):
        return response.Response(BookArtisan.objects.aggregate(
            all=Count("pk"), **{name: Count("pk", filter=condition) for name, condition in BOOKING_FILTERS.items()}
        ))

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        serializer = ResolveDisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        outcome, note = serializer.validated_data["outcome"], serializer.validated_data["note"]

        with transaction.atomic():
            booking = (BookArtisan.objects.select_for_update(of=("self",)).select_related("client", "artisan")
                       .filter(pk=pk).first())
            if booking is None:
                raise Http404
            if booking.status != "Disputed":
                return response.Response({"detail": "This booking has no open problem."}, status=status.HTTP_400_BAD_REQUEST)

            if outcome == "refund":
                if not booking.payment_reference:
                    return response.Response(
                        {"detail": "We don't have this payment's Paystack reference. Refund it in the Paystack dashboard."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                try:
                    refund_charge(booking.payment_reference)
                except PaystackError as error:
                    return response.Response({"detail": f"Paystack: {error}"}, status=status.HTTP_400_BAD_REQUEST)

            booking.resolved_at = timezone.now()
            booking.resolved_by = request.user
            booking.resolution_note = note
            if outcome == "release":
                release_booking(booking, client_message=(
                    f"We've looked into the problem you reported and paid {booking.artisan.first_name} for the job. {note}"
                ))
            else:
                booking.status = "Refunded"
                booking.save()
                Notification.objects.create(
                    recipient=booking.client,
                    message=f"We've refunded ₦{booking.agreed_amount:,} for the problem you reported. "
                            f"It can take a few days to reach your account. {note}",
                    notification_type="PAYMENT"
                )
                if booking.artisan:
                    Notification.objects.create(
                        recipient=booking.artisan,
                        message=f"We refunded {booking.client.first_name} for the job you were booked for. {note}",
                        notification_type="BOOKING"
                    )
        logger.info(f"Problem on booking {booking.id} resolved by {request.user.email}: {outcome}")
        return response.Response(self.get_serializer(booking).data)
