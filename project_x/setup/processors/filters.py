from rest_framework import filters
from datetime import datetime
from django.db.models import Q


class BookingScheduleFilter(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        user = request.user
        schedule = request.query_params.get('schedule', None)
        today = datetime.today()

        if schedule is None or schedule != "today":
            return queryset

        date = today.date()  # ✅ FIXED — was today.date (missing parentheses)
        return queryset.filter(Q(client=user) | Q(artisan=user), scheduled_date=date)