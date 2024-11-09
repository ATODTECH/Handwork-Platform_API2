from rest_framework import filters
from django.utils.dateparse import parse_date
from django.db.models import Q
from rest_framework.exceptions import ValidationError

class DateSearchFilter(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):

        # Get the date from the query parameters
        date_str = request.query_params.get('date', None)

        if date_str is None:
            return queryset # Return unfiltered queryset if no date is provided

        # Parse the date from the string
        date = parse_date(date_str)
        if date is None:
            raise ValidationError({"detail": "Invalid date format. Use YYYY-MM-DD format."})
        
        return queryset.filter(scheduled_date=date)