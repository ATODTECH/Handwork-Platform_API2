from rest_framework import filters
from django.utils.dateparse import parse_date
from datetime import datetime
from django.db.models import Q
from rest_framework.exceptions import ValidationError

class BookingScheduleFilter(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):

        # Get the date from the query parameters
        schedule = request.query_params.get('schedule', None)
        today = datetime.today()

        if schedule is None or schedule != "today":
            return queryset # Return unfiltered queryset if no date is provided
        
        date = datetime.today()

        return queryset.filter(scheduled_date=date)