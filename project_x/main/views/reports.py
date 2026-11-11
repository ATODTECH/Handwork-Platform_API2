"""
Reporting accounts, requests, offers, messages, bookings and reviews, and staff settling those reports.
Reports about the same thing are settled together.
"""
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from chat.messaging import is_participant, request_is_visible_to_provider
from chat.models import Message
from main.models import Notification, Rating, Report, User
from main.serializers.report_serializers import (
    AdminReportSerializer, CreateReportSerializer, MyReportSerializer, ResolveReportSerializer,
)
from services.models.job_models import BookArtisan
from services.models.marketplace_models import JobRequest, Offer
from services.views.admin_marketplace import staff_close_request, staff_withdraw_offer

logger = logging.getLogger("main")

# Enough for anyone with a real problem; stops one account flooding staff
MAX_REPORTS_PER_DAY = 20

TARGET_LABELS = {"USER": "account", "REQUEST": "request", "OFFER": "offer", "MESSAGE": "message",
                 "BOOKING": "booking", "REVIEW": "review"}


def find(model, pk, **filters):
    try:
        return model.objects.filter(pk=pk, **filters).first()
    except (ValueError, DjangoValidationError):
        return None


def report_target(user, target_type, target_id):
    """
    The fields a report about this target gets, including who's responsible. Only what the reporter can
    see may be reported; anything else is "not found", so reports can't be used to probe for ids.
    """
    if target_type == "USER":
        reported = find(User, target_id)
        if reported is None or reported.pk == user.pk:
            raise NotFound("We couldn't find that account.")
        return {"reported_user": reported}

    if target_type == "REQUEST":
        job_request = find(JobRequest, target_id)
        if (job_request is None or user.role != "ARTISAN"
                or not request_is_visible_to_provider(job_request, user)):
            raise NotFound("We couldn't find that request.")
        return {"request": job_request, "reported_user": job_request.customer}

    if target_type == "OFFER":
        offer = find(Offer, target_id, request__customer=user)
        if offer is None:
            raise NotFound("We couldn't find that offer.")
        return {"offer": offer, "request": offer.request, "reported_user": offer.provider}

    if target_type == "MESSAGE":
        message = find(Message, target_id)
        if message is None or message.sender_id == user.pk:
            raise NotFound("We couldn't find that message.")
        if message.booking_id:
            allowed = is_participant(message.booking, user)
        else:
            allowed = user.pk in (message.request.customer_id, message.provider_id)
        if not allowed:
            raise NotFound("We couldn't find that message.")
        return {"message": message, "booking": message.booking, "request": message.request,
                "reported_user": message.sender}

    if target_type == "BOOKING":
        booking = find(BookArtisan, target_id)
        if booking is None or not is_participant(booking, user):
            raise NotFound("We couldn't find that booking.")
        other = booking.artisan if user.pk == booking.client_id else booking.client
        return {"booking": booking, "reported_user": other}

    if target_type == "REVIEW":
        review = find(Rating, target_id, hidden_at__isnull=True)
        if review is None or review.client_id == user.pk:
            raise NotFound("We couldn't find that review.")
        return {"review": review, "booking": review.booking, "reported_user": review.client}

    raise ValidationError({"target_type": ["Choose what you're reporting."]})


class ReportViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """POST to report something; GET your own reports and whether they've been looked at"""
    permission_classes = [IsAuthenticated]
    serializer_class = MyReportSerializer

    def get_queryset(self):
        return Report.objects.filter(reporter=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = CreateReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = report_target(request.user, data["target_type"], data["target_id"])

        report = Report(reporter=request.user, target_type=data["target_type"], reason=data["reason"],
                        details=data.get("details", ""), **target)
        if Report.objects.filter(reporter=request.user, status="OPEN", **report.target_key()).exists():
            return Response({"detail": "You've already reported this. Our team is looking at it."},
                            status=status.HTTP_400_BAD_REQUEST)
        since = timezone.now() - timedelta(days=1)
        if Report.objects.filter(reporter=request.user, created_at__gte=since).count() >= MAX_REPORTS_PER_DAY:
            return Response({"detail": "You've sent a lot of reports today. Contact support if something is urgent."},
                            status=status.HTTP_400_BAD_REQUEST)
        report.save()
        logger.info(f"Report {report.id}: {report.target_type} reported by {request.user.email} ({report.reason})")
        return Response(MyReportSerializer(report).data, status=status.HTTP_201_CREATED)


def hide_content(report, note, staff):
    """Remove what was reported. Returns an error message when it can't be hidden this way."""
    now = timezone.now()
    if report.target_type == "REVIEW":
        Rating.objects.filter(pk=report.review_id).update(hidden_at=now, hidden_reason=note)
    elif report.target_type == "MESSAGE":
        Message.objects.filter(pk=report.message_id).update(hidden_at=now)
    elif report.target_type == "OFFER":
        offer = Offer.objects.select_for_update().get(pk=report.offer_id)
        # Already withdrawn or turned down: nothing left to remove
        if offer.status in ("SUBMITTED", "ACCEPTED"):
            return staff_withdraw_offer(offer)
    elif report.target_type == "REQUEST":
        job_request = JobRequest.objects.select_for_update().get(pk=report.request_id)
        if job_request.status != "CLOSED":
            return staff_close_request(job_request, note)
    elif report.target_type == "USER":
        return "To stop an account, deactivate it on the Users page."
    elif report.target_type == "BOOKING":
        return "A booking can't be hidden. Use the Bookings page to settle it."
    return None


REPORT_FILTERS = {
    "open": Q(status="OPEN"),
    "actioned": Q(status="ACTIONED"),
    "dismissed": Q(status="DISMISSED"),
}


class AdminReportViewSet(viewsets.ReadOnlyModelViewSet):
    """Every report (?status=open|actioned|dismissed, ?type=REVIEW...), `counts`, and `resolve`"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminReportSerializer

    def get_queryset(self):
        queryset = Report.objects.select_related(
            "reporter", "reported_user", "resolved_by", "request", "offer", "offer__request", "message",
            "booking", "review",
        ).prefetch_related("booking__services")
        status_filter = REPORT_FILTERS.get(self.request.query_params.get("status", ""))
        if status_filter is not None:
            queryset = queryset.filter(status_filter)
        if self.request.query_params.get("type"):
            queryset = queryset.filter(target_type=self.request.query_params["type"].upper())
        return queryset

    @action(detail=False, methods=["get"])
    def counts(self, request):
        totals = Report.objects.aggregate(
            all=Count("pk"), **{name: Count("pk", filter=condition) for name, condition in REPORT_FILTERS.items()}
        )
        open_by_type = dict(Report.objects.filter(status="OPEN").values_list("target_type").annotate(Count("pk")))
        return Response({**totals, "open_by_type": open_by_type})

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        serializer = ResolveReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        outcome, note, hide = (serializer.validated_data[key] for key in ("outcome", "note", "hide"))

        with transaction.atomic():
            report = get_object_or_404(Report.objects.select_for_update().select_related("reported_user"), pk=pk)
            if report.status != "OPEN":
                return Response({"detail": "This report has already been settled."}, status=status.HTTP_400_BAD_REQUEST)
            if hide:
                problem = hide_content(report, note, request.user)
                if problem:
                    return Response({"detail": problem}, status=status.HTTP_400_BAD_REQUEST)

            # Every open report about the same thing is settled with this one
            reports = list(Report.objects.select_for_update().filter(status="OPEN", **report.target_key())
                           .select_related("reporter"))
            now = timezone.now()
            for each in reports:
                each.status = "ACTIONED" if outcome == "action" else "DISMISSED"
                each.resolution_note = note
                each.resolved_by = request.user
                each.resolved_at = now
                each.content_hidden = hide
                each.save()

        thanks = ("Thanks for your report. We've looked into it and taken action." if outcome == "action" else
                  "Thanks for your report. We've looked into it and didn't find anything against our rules.")
        for reporter in {each.reporter for each in reports}:
            Notification.objects.create(recipient=reporter, message=thanks, notification_type="VERIFICATION")
        if hide and report.reported_user:
            Notification.objects.create(
                recipient=report.reported_user, notification_type="VERIFICATION",
                message=f"Our team removed your {TARGET_LABELS[report.target_type]}: {note}",
            )
        logger.info(f"Report {report.id} ({len(reports)} in all) {outcome} by {request.user.email}; hidden: {hide}")
        report.refresh_from_db()
        return Response(self.get_serializer(report).data)
