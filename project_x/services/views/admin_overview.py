"""The staff dashboard's figures and short lists, in one call."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework import response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from main.models import User
from services.models.job_models import BookArtisan, Transaction, Withdrawal

LIST_LIMIT = 5
RECENT_USERS_LIMIT = 8
# Paid by the customer and held by us until they confirm the job (not old deposit bookings)
HELD_STATUSES = ("Paid", "In Progress", "Completed", "Disputed")
ACTIVE_STATUSES = ("Accepted", "Paid", "Deposit Paid", "In Progress", "Completed", "Disputed")


def full_name(user):
    return f"{user.first_name} {user.last_name}".strip() or user.email


def month_starts(now):
    this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    return this_month, last_month


def this_and_last_month(queryset, field, this_month, last_month, total=None):
    """{this_month, last_month} for a queryset: a sum of `total` if given, else a count"""
    def measure(**filters):
        subset = queryset.filter(**filters)
        if total:
            return subset.aggregate(value=Sum(total))["value"] or Decimal("0")
        return subset.count()
    return {
        "this_month": measure(**{f"{field}__gte": this_month}),
        "last_month": measure(**{f"{field}__gte": last_month, f"{field}__lt": this_month}),
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_overview(request):
    this_month, last_month = month_starts(timezone.now())

    earnings = Transaction.objects.filter(transaction_type="EARNING")
    held = BookArtisan.objects.filter(status__in=HELD_STATUSES, deposit_paid_at__isnull=True)
    customers = User.objects.filter(role="USER", is_staff=False)
    artisans = User.objects.filter(role="ARTISAN")
    bookings = BookArtisan.objects.all()
    pending_withdrawals = Withdrawal.objects.filter(status="PENDING")

    awaiting_review = (artisans.filter(onboardingComplete=True, verification_status="PENDING")
                       .prefetch_related("skill_set__services").order_by("-date_joined"))
    recent_bookings = (bookings.select_related("client", "artisan").prefetch_related("services")
                       .order_by("-created_at")[:LIST_LIMIT])
    recent_users = User.objects.filter(role__in=("USER", "ARTISAN"), is_staff=False).order_by("-date_joined")[:RECENT_USERS_LIMIT]

    return response.Response({
        "revenue": {
            # Our 10% fee on jobs customers have confirmed
            **this_and_last_month(earnings, "created_at", this_month, last_month, total="platform_fee"),
            "total": earnings.aggregate(value=Sum("platform_fee"))["value"] or Decimal("0"),
        },
        "held": {
            "amount": held.aggregate(value=Sum("agreed_amount"))["value"] or Decimal("0"),
            "bookings": held.count(),
        },
        "customers": {
            "total": customers.count(),
            "new_this_month": customers.filter(date_joined__gte=this_month).count(),
        },
        "artisans": {
            "approved": artisans.filter(verification_status="APPROVED").count(),
            "awaiting_review": awaiting_review.count(),
            "onboarding": artisans.filter(onboardingComplete=False).count(),
        },
        "bookings": {
            **this_and_last_month(bookings, "created_at", this_month, last_month),
            "active": bookings.filter(status__in=ACTIVE_STATUSES).count(),
            # Problems customers reported, waiting for staff to settle
            "disputed": bookings.filter(status="Disputed").count(),
        },
        "withdrawals": {
            "pending": pending_withdrawals.count(),
            "pending_amount": pending_withdrawals.aggregate(value=Sum("amount"))["value"] or Decimal("0"),
        },
        "artisans_awaiting_review": [{
            "id": str(artisan.id),
            "name": full_name(artisan),
            "services": [service.name for skill in artisan.skill_set.all() for service in skill.services.all()],
            "city": artisan.city,
            "state": artisan.state,
            "submitted_at": artisan.date_joined,
        } for artisan in awaiting_review[:LIST_LIMIT]],
        "withdrawals_awaiting_approval": [{
            "id": withdrawal.id,
            "artisan": full_name(withdrawal.artisan),
            "amount": withdrawal.amount,
            "bank_name": withdrawal.bank_name,
            "requested_at": withdrawal.created_at,
        } for withdrawal in pending_withdrawals.select_related("artisan").order_by("created_at")[:LIST_LIMIT]],
        "recent_bookings": [{
            "id": booking.id,
            "customer": full_name(booking.client),
            "artisan": full_name(booking.artisan) if booking.artisan else None,
            "services": [service.name for service in booking.services.all()],
            "amount": booking.agreed_amount or booking.budget,
            "status": booking.status,
            "created_at": booking.created_at,
        } for booking in recent_bookings],
        "recent_users": [{
            "id": str(user.id),
            "name": full_name(user),
            "email": user.email,
            "role": user.role,
            "verification_status": user.verification_status if user.role == "ARTISAN" else None,
            "date_joined": user.date_joined,
        } for user in recent_users],
    })
