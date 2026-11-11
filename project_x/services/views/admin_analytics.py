"""
Marketplace figures for the staff Reports page: month by month for a chosen window, totals against the
window before it, the request-to-job funnel, and the top categories and artisans. Months are Lagos months.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Avg, Count, Min, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework import response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from main.models import Rating, Report, User
from services.models.job_models import BookArtisan, Transaction
from services.models.marketplace_models import JobRequest, Offer
from services.models.services_models import Category

LAGOS = ZoneInfo("Africa/Lagos")
DEFAULT_MONTHS = 6
MIN_MONTHS, MAX_MONTHS = 3, 24
TOP_LIMIT = 5
ZERO = Decimal("0")


def shift_month(start, months):
    """The first of the month `months` away from `start` (a first of the month), in Lagos time"""
    index = start.year * 12 + start.month - 1 + months
    return start.replace(year=index // 12, month=index % 12 + 1)


def window(months, now=None):
    """(start, end, previous_start) for the last `months` months including this one"""
    now = (now or timezone.now()).astimezone(LAGOS)
    this_month = datetime(now.year, now.month, 1, tzinfo=LAGOS)
    start = shift_month(this_month, -(months - 1))
    return start, shift_month(this_month, 1), shift_month(start, -months)


def month_keys(start, months):
    return [shift_month(start, offset).strftime("%Y-%m") for offset in range(months)]


def monthly(queryset, field, keys, total=None):
    """A value per month key: a count, or the sum of `total`"""
    rows = (queryset.annotate(month=TruncMonth(field, tzinfo=LAGOS)).values("month")
            .annotate(value=Sum(total) if total else Count("pk", distinct=True)))
    by_month = {row["month"].astimezone(LAGOS).strftime("%Y-%m"): row["value"] for row in rows if row["month"]}
    empty = ZERO if total else 0
    return [by_month.get(key) or empty for key in keys]


def between(queryset, field, start, end):
    return queryset.filter(**{f"{field}__gte": start, f"{field}__lt": end})


def totals(start, end):
    """The headline figures for one window"""
    earnings = between(Transaction.objects.filter(transaction_type="EARNING"), "created_at", start, end)
    money = earnings.aggregate(fees=Sum("platform_fee"), job_value=Sum("gross_amount"), paid_out=Sum("net_amount"))
    bookings = BookArtisan.objects.all()
    refunded = between(bookings.filter(status="Refunded"), "resolved_at", start, end)
    requests = between(JobRequest.objects.exclude(status="DRAFT"), "published_at", start, end)
    reviews = between(Rating.objects.filter(booking__isnull=False, hidden_at__isnull=True), "created_at", start, end)
    return {
        # Our fee, the value of jobs released to artisans, and what artisans received
        "fees": money["fees"] or ZERO,
        "job_value": money["job_value"] or ZERO,
        "paid_to_artisans": money["paid_out"] or ZERO,
        "bookings": between(bookings, "created_at", start, end).count(),
        "completed": earnings.values("booking").distinct().count(),
        "cancelled": between(bookings.filter(status__in=("Cancelled", "Declined")), "updated_at", start, end).count(),
        "problems_reported": between(bookings, "disputed_at", start, end).count(),
        "refunds": refunded.count(),
        "refunded_amount": refunded.aggregate(value=Sum("agreed_amount"))["value"] or ZERO,
        "new_customers": between(User.objects.filter(role="USER", is_staff=False), "date_joined", start, end).count(),
        "new_artisans": between(User.objects.filter(role="ARTISAN"), "date_joined", start, end).count(),
        "artisans_approved": between(User.objects.filter(role="ARTISAN", verification_status="APPROVED"),
                                     "verified_at", start, end).count(),
        "requests": requests.count(),
        "offers": between(Offer.objects.all(), "created_at", start, end).count(),
        "reviews": reviews.count(),
        "average_rating": round(reviews.aggregate(value=Avg("star"))["value"] or 0, 1) or None,
        "reports": between(Report.objects.all(), "created_at", start, end).count(),
    }


def request_funnel(start, end):
    """Of the requests posted in the window: how many got an offer, had one accepted, and were finished"""
    requests = between(JobRequest.objects.exclude(status="DRAFT"), "published_at", start, end)
    first_offers = requests.annotate(first_offer=Min("offers__created_at")).filter(first_offer__isnull=False)
    waits = [(item.first_offer - item.published_at).total_seconds() / 3600 for item in first_offers]
    posted = requests.count()
    offers = Offer.objects.filter(request__in=requests).count()
    return {
        "posted": posted,
        "with_offer": first_offers.count(),
        "accepted": requests.filter(status="ACCEPTED").count(),
        "completed": requests.filter(booking__status__in=("Released", "Paid in Full")).count(),
        "average_offers": round(offers / posted, 1) if posted else 0,
        # Middle value, so one slow request doesn't skew it
        "median_hours_to_first_offer": round(sorted(waits)[len(waits) // 2], 1) if waits else None,
    }


def top_categories(start, end):
    rows = []
    for category in Category.objects.prefetch_related("service"):
        service_ids = [service.id for service in category.service.all()]
        if not service_ids:
            continue
        bookings = between(BookArtisan.objects.filter(services__in=service_ids), "created_at", start, end).distinct()
        requests = between(JobRequest.objects.exclude(status="DRAFT").filter(
            Q(category=category) | Q(services__in=service_ids)), "published_at", start, end).distinct()
        value = (Transaction.objects.filter(transaction_type="EARNING", booking__in=bookings.values("pk"))
                 .aggregate(value=Sum("gross_amount"))["value"] or ZERO)
        rows.append({"name": category.name, "bookings": bookings.count(), "requests": requests.count(), "job_value": value})
    rows.sort(key=lambda row: (row["bookings"], row["requests"], row["job_value"]), reverse=True)
    return [row for row in rows if row["bookings"] or row["requests"]][:TOP_LIMIT]


def top_artisans(start, end):
    earnings = between(Transaction.objects.filter(transaction_type="EARNING"), "created_at", start, end)
    rows = (earnings.values("artisan", "artisan__first_name", "artisan__last_name")
            .annotate(jobs=Count("booking", distinct=True), earned=Sum("net_amount"), job_value=Sum("gross_amount"))
            .order_by("-job_value")[:TOP_LIMIT])
    return [{
        "id": str(row["artisan"]),
        "name": f"{row['artisan__first_name']} {row['artisan__last_name']}".strip(),
        "jobs": row["jobs"],
        "job_value": row["job_value"] or ZERO,
        "earned": row["earned"] or ZERO,
    } for row in rows]


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_analytics(request):
    try:
        months = int(request.query_params.get("months", DEFAULT_MONTHS))
    except ValueError:
        months = DEFAULT_MONTHS
    months = max(MIN_MONTHS, min(MAX_MONTHS, months))
    start, end, previous_start = window(months)
    keys = month_keys(start, months)

    earnings = between(Transaction.objects.filter(transaction_type="EARNING"), "created_at", start, end)
    bookings = between(BookArtisan.objects.all(), "created_at", start, end)
    published = between(JobRequest.objects.exclude(status="DRAFT"), "published_at", start, end)

    return response.Response({
        "months": keys,
        "from": start.date(),
        "to": end.date(),
        "series": {
            "fees": monthly(earnings, "created_at", keys, total="platform_fee"),
            "job_value": monthly(earnings, "created_at", keys, total="gross_amount"),
            "bookings": monthly(bookings, "created_at", keys),
            "completed": monthly(earnings, "created_at", keys),
            "requests": monthly(published, "published_at", keys),
            "new_customers": monthly(between(User.objects.filter(role="USER", is_staff=False), "date_joined", start, end),
                                     "date_joined", keys),
            "new_artisans": monthly(between(User.objects.filter(role="ARTISAN"), "date_joined", start, end),
                                    "date_joined", keys),
        },
        "totals": totals(start, end),
        # The same figures for the window just before, to compare against
        "previous": totals(previous_start, start),
        "funnel": request_funnel(start, end),
        "top_categories": top_categories(start, end),
        "top_artisans": top_artisans(start, end),
    })
