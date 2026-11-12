"""
What staff need to know about, built from what's already recorded (no notification table): artisans waiting
for review, problems customers report and how they were settled, withdrawals, failed payouts and reports.
Anything still waiting on staff is always listed, however old; the rest covers the last `days` days.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from main.models import Report, User
from services.models.job_models import BookArtisan, Withdrawal
from support.models import SupportTicket

DEFAULT_DAYS = 30
MAX_DAYS = 90
MAX_ITEMS = 200

REPORT_NOUNS = {"USER": "an account", "REQUEST": "a request", "OFFER": "an offer", "MESSAGE": "a message",
                "BOOKING": "a booking", "REVIEW": "a review"}


def full_name(user):
    return (f"{user.first_name} {user.last_name}".strip() or user.email) if user else "Someone"


def naira(amount):
    return f"₦{amount:,.0f}"


def item(kind, key, at, title, detail, needs_action, page, target=None):
    return {"id": f"{kind.lower()}-{key}", "kind": kind, "at": at, "title": title, "detail": detail,
            "needs_action": needs_action, "link": {"page": page, "id": target}}


def artisan_items(since):
    """Every artisan who finished onboarding and is waiting for staff, however long ago they signed up"""
    artisans = User.objects.filter(role="ARTISAN", is_active=True, onboardingComplete=True, verification_status="PENDING")
    for artisan in artisans:
        yield item("ARTISAN_REVIEW", artisan.pk, artisan.date_joined, f"{full_name(artisan)} is waiting for review",
                   f"{artisan.city or 'No city'}, {artisan.state or 'no state'}. Approve them so customers can book them.",
                   True, "artisans", str(artisan.pk))


def booking_items(since):
    problems = (BookArtisan.objects.select_related("client", "artisan", "resolved_by")
                .filter(Q(status="Disputed") | Q(disputed_at__gte=since) | Q(resolved_at__gte=since))
                .filter(disputed_at__isnull=False))
    for booking in problems:
        open_problem = booking.status == "Disputed"
        if open_problem or booking.disputed_at >= since:
            yield item("PROBLEM_REPORTED", booking.pk, booking.disputed_at,
                       f"{full_name(booking.client)} reported a problem on booking #{booking.pk}",
                       booking.dispute_reason[:200] or "No reason given.", open_problem, "bookings", booking.pk)
        if booking.resolved_at and booking.resolved_at >= since:
            outcome = "refunded the customer" if booking.status == "Refunded" else "paid the artisan"
            yield item("PROBLEM_SETTLED", booking.pk, booking.resolved_at,
                       f"Booking #{booking.pk} settled: {full_name(booking.resolved_by)} {outcome}",
                       booking.resolution_note[:200], False, "bookings", booking.pk)


def withdrawal_items(since):
    withdrawals = (Withdrawal.objects.select_related("artisan")
                   .filter(Q(status="PENDING") | Q(created_at__gte=since) | Q(status="FAILED", reviewed_at__gte=since)))
    for withdrawal in withdrawals:
        name = full_name(withdrawal.artisan)
        if withdrawal.status == "PENDING" or withdrawal.created_at >= since:
            yield item("WITHDRAWAL_REQUESTED", withdrawal.pk, withdrawal.created_at,
                       f"{name} asked to withdraw {naira(withdrawal.amount)}",
                       f"To {withdrawal.bank_name}.", withdrawal.status == "PENDING", "payouts", withdrawal.pk)
        if withdrawal.status == "FAILED":
            at = withdrawal.completed_at or withdrawal.reviewed_at or withdrawal.created_at
            if at >= since:
                yield item("PAYOUT_FAILED", withdrawal.pk, at, f"Payout of {naira(withdrawal.amount)} to {name} failed",
                           (withdrawal.note or "Paystack didn't complete the transfer.")[:200] + " The money is back in their wallet.",
                           False, "payouts", withdrawal.pk)


def report_items(since):
    reports = Report.objects.select_related("reporter", "reported_user").filter(Q(status="OPEN") | Q(created_at__gte=since))
    for report in reports:
        about = f" about {full_name(report.reported_user)}" if report.reported_user else ""
        yield item("REPORT", report.pk, report.created_at,
                   f"{full_name(report.reporter)} reported {REPORT_NOUNS[report.target_type]}{about}",
                   f"{report.get_reason_display()}. {report.details[:160]}".strip(), report.status == "OPEN",
                   "reported-content", report.pk)


def support_items(since):
    """Tickets waiting on staff (always), and ones that came in during the window"""
    tickets = SupportTicket.objects.select_related("requester").filter(Q(status="OPEN") | Q(created_at__gte=since))
    for ticket in tickets:
        waiting = ticket.status == "OPEN"
        yield item("SUPPORT", ticket.pk, ticket.last_message_at if waiting else ticket.created_at,
                   f"{ticket.contact_name}: {ticket.subject}",
                   f"{ticket.reference} · {ticket.get_category_display()}" + (" · new message" if ticket.staff_unread else ""),
                   waiting, "support", ticket.pk)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_notifications(request):
    try:
        days = int(request.query_params.get("days", DEFAULT_DAYS))
    except ValueError:
        days = DEFAULT_DAYS
    days = max(1, min(MAX_DAYS, days))
    since = timezone.now() - timedelta(days=days)

    items = [*artisan_items(since), *booking_items(since), *withdrawal_items(since), *report_items(since),
             *support_items(since)]
    # Waiting on staff first, then newest
    items.sort(key=lambda entry: entry["at"], reverse=True)
    items.sort(key=lambda entry: not entry["needs_action"])
    by_kind = {}
    for entry in items:
        if entry["needs_action"]:
            by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
    return response.Response({
        "days": days,
        "needs_action": sum(by_kind.values()),
        "needs_action_by_kind": by_kind,
        "items": items[:MAX_ITEMS],
    })
