from . import BookArtisan, ArtisanBookingSerializers, User
from rest_framework import viewsets, response, status, filters
from rest_framework.decorators import api_view
from django.shortcuts import get_list_or_404, get_object_or_404
from django.db.models import Q
from setup.processors.filters import BookingScheduleFilter
from django.db import transaction
from datetime import datetime
from rest_framework.response import Response


class BookArtisanViewSet(viewsets.ModelViewSet):
    queryset = BookArtisan.objects.select_related('client', 'artisan').prefetch_related('services')
    serializer_class = ArtisanBookingSerializers


@api_view(['GET'])
def get_my_bookings(request):
    # Get the current user
    user = request.user

    # Retrieve bookings for either artisan or client
    try:
        bookings = BookArtisan.objects.filter(Q(client=user) | Q(artisan=user))
        serialized_data = ArtisanBookingSerializers(bookings, many=True).data
    except BookArtisan.DoesNotExist:
        return None    
    return response.Response(serialized_data, status=status.HTTP_200_OK) 


class MyFilteredBookings(viewsets.ModelViewSet):
    serializer_class = ArtisanBookingSerializers
    # filter_backends = [BookingScheduleFilter]

    def get_queryset(self):
        user = self.request.user
        try:
            bookings = BookArtisan.objects.filter(Q(status="Pending"), (Q(client=user) | Q(artisan=user))).select_related("client", "artisan").prefetch_related("services")
        except BookArtisan.DoesNotExist:
            return None
        
        return bookings
    
    def retrieve(self, request):
        user = request.user
        date_today = datetime.today().date()
        booking_id = request.query_params.get("booking_id")
        schedule_filter = request.query_params.get("schedule", None)

        try:
            if schedule_filter is not None and schedule_filter.lower() == "today":
                # Filter bookings for today
                bookings = BookArtisan.objects.filter(
                    Q(client=user) | Q(artisan=user), scheduled_date=date_today
                )
                if bookings.exists():
                    # Serialize and return the data
                    serialized_data = self.get_serializer(bookings, many=True).data
                    return Response(serialized_data, status=status.HTTP_200_OK)
                return response.Response(
                    {"message": "No bookings scheduled for today."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Handle booking retrieval by ID
            if booking_id:
                booking = BookArtisan.objects.filter(artisan=user, id=booking_id).first()
                if booking:
                    # Serialize and return the data
                    serialized_data = self.get_serializer(booking).data
                    return Response(serialized_data, status=status.HTTP_200_OK)
                return response.Response(
                    {"message": "No booking found with the provided ID."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Default response for missing parameters
            return response.Response(
                {"message": "Please provide a valid schedule or booking_id parameter."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            # Catch and log unexpected errors
            return response.Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    

@api_view(['POST'])
def toggle_availability(request):
    user = request.user
    availability = request.data.get("isAvailable")

    # Ensure the `availability` key is provided
    if availability is None:
        return response.Response({"error": "The 'availability' field is required."}, status=status.HTTP_400_BAD_REQUEST)

    # Ensure the value is a boolean
    if not isinstance(availability, bool):
        return response.Response({"error": "The 'availability' field must be a boolean (true or false)."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        artisan = get_object_or_404(User.objects.select_for_update(), id=user.id)
        artisan.isAvailable = availability
        artisan.save()

    return response.Response({"message": "Availability toggled successfully!"}, status=status.HTTP_200_OK)


# def get_today_bookings(request):
#     user = request.user
#     today = datetime.today()
#     try:
#         bookings = BookArtisan.objects.filter(Q(client=user) | Q(artisan=user), created_at=today)
#     except BookArtisan.DoesNotExist:
#         return None

@api_view(['GET'])
def get_today_bookings(request):
    user = request.user
    date_today = datetime.today().date()
    booking_id = request.query_params.get("booking_id")
    schedule_filter = request.query_params.get("schedule", None)

    try:
        if schedule_filter is not None and schedule_filter.lower() == "today":
            # Filter bookings for today
            bookings = BookArtisan.objects.filter(
                Q(client=user) | Q(artisan=user), scheduled_date=date_today
            )
            if bookings.exists():
                # Serialize and return the data
                serialized_data = ArtisanBookingSerializers(bookings, many=True).data
                return Response(serialized_data, status=status.HTTP_200_OK)
            return Response(
                {"message": "No bookings scheduled for today."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Handle booking retrieval by ID
        if booking_id:
            booking = BookArtisan.objects.filter(artisan=user, id=booking_id).first()
            if booking:
                # Serialize and return the data
                serialized_data = ArtisanBookingSerializers(booking).data
                return Response(serialized_data, status=status.HTTP_200_OK)
            return Response(
                {"message": "No booking found with the provided ID."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Default response for missing parameters
        return Response(
            {"message": "Please provide a valid schedule or booking_id parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        # Catch and log unexpected errors
        return Response(
            {"error": f"An unexpected error occurred: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )