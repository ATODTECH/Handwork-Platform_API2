"""
Staff moderating the request-and-offer marketplace: every request with its offers, closing a request that
breaks the rules, and withdrawing an offer. Once a request has a booking, the Bookings page takes over.
"""
import logging

from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from main.models import Notification
from services.models.marketplace_models import JobRequest, Offer
from services.serializers.admin_marketplace_serializers import (
    AdminOfferSerializer, AdminRequestDetailSerializer, AdminRequestSerializer, StaffActionSerializer,
)
from services.views.marketplace import with_offer_counts

logger = logging.getLogger("services")

REMOVED = "Removed by our team."

REQUEST_FILTERS = {
    "open": Q(status="OPEN"),
    "booked": Q(status="ACCEPTED"),
    "closed": Q(status="CLOSED"),
    "draft": Q(status="DRAFT"),
}


def notify(user, message):
    Notification.objects.create(recipient=user, message=message, notification_type="BOOKING")


def staff_close_request(job_request, reason):
    """
    Close a draft or open request for breaking the rules, and turn down its waiting offers, telling those
    providers. The caller holds a row lock and tells the customer. Returns an error when it can't be closed.
    """
    if job_request.status == "ACCEPTED":
        return "This request has a booking. Settle it from the Bookings page."
    if job_request.status not in ("DRAFT", "OPEN"):
        return "This request is already closed."
    job_request.status = "CLOSED"
    job_request.closed_at = timezone.now()
    job_request.close_reason = f"{REMOVED} {reason}"
    job_request.save()
    for offer in job_request.offers.filter(status="SUBMITTED").select_related("provider"):
        offer.status = "REJECTED"
        offer.rejection_note = "The request was removed."
        offer.save(update_fields=["status", "rejection_note", "updated_at"])
        notify(offer.provider, f"\"{job_request.title}\" was removed, so your offer is closed.")
    return None


def staff_withdraw_offer(offer):
    """Withdraw a waiting offer for breaking the rules. The caller holds a row lock and tells the people involved."""
    if offer.status == "ACCEPTED":
        return "This offer was accepted and has a booking. Settle it from the Bookings page."
    if offer.status != "SUBMITTED":
        return "This offer is no longer waiting for an answer."
    offer.status = "WITHDRAWN"
    offer.rejection_note = REMOVED
    offer.save(update_fields=["status", "rejection_note", "updated_at"])
    return None


class AdminRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """Every request (?status=open|booked|closed|draft, ?search=), `counts`, and `close`"""
    permission_classes = [IsAdminUser]

    def get_serializer_class(self):
        return AdminRequestDetailSerializer if self.action in ("retrieve", "close") else AdminRequestSerializer

    def get_queryset(self):
        queryset = with_offer_counts(
            JobRequest.objects.select_related("customer", "target_provider", "category", "booking")
            .prefetch_related("services")
        )
        if self.action in ("retrieve", "close"):
            queryset = queryset.prefetch_related(
                "attachments",
                Prefetch("offers", queryset=Offer.objects.select_related("provider").prefetch_related("revisions")),
            )
        status_filter = REQUEST_FILTERS.get(self.request.query_params.get("status", ""))
        if status_filter is not None:
            queryset = queryset.filter(status_filter)
        search = self.request.query_params.get("search", "").strip()
        if search:
            matches = (Q(title__icontains=search) | Q(customer__first_name__icontains=search)
                       | Q(customer__last_name__icontains=search) | Q(customer__email__icontains=search))
            digits = search.upper().removeprefix("REQ-").lstrip("0")
            if digits.isdigit():
                matches |= Q(pk=int(digits))
            queryset = queryset.filter(matches)
        return queryset.order_by("-created_at")

    @action(detail=False, methods=["get"])
    def counts(self, request):
        return Response(JobRequest.objects.aggregate(
            all=Count("pk"), **{name: Count("pk", filter=condition) for name, condition in REQUEST_FILTERS.items()}
        ))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        serializer = StaffActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        with transaction.atomic():
            job_request = get_object_or_404(JobRequest.objects.select_for_update(of=("self",)), pk=pk)
            problem = staff_close_request(job_request, reason)
            if problem:
                return Response({"detail": problem}, status=status.HTTP_400_BAD_REQUEST)
        notify(job_request.customer, f"Our team removed your request \"{job_request.title}\": {reason}")
        logger.info(f"Request {job_request.id} closed by staff {request.user.email}: {reason}")
        return Response(self.get_serializer(self.get_queryset().get(pk=job_request.pk)).data)


class AdminOfferViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """One offer, and `withdraw` to remove it"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminOfferSerializer
    queryset = Offer.objects.select_related("provider", "request", "request__customer").prefetch_related("revisions")

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        serializer = StaffActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        with transaction.atomic():
            offer = get_object_or_404(Offer.objects.select_for_update(of=("self",)), pk=pk)
            problem = staff_withdraw_offer(offer)
            if problem:
                return Response({"detail": problem}, status=status.HTTP_400_BAD_REQUEST)
        offer = self.get_queryset().get(pk=offer.pk)
        notify(offer.provider, f"Our team removed your offer on \"{offer.request.title}\": {reason}")
        notify(offer.request.customer, f"An offer on \"{offer.request.title}\" was removed by our team.")
        logger.info(f"Offer {offer.id} withdrawn by staff {request.user.email}: {reason}")
        return Response(self.get_serializer(offer).data)
