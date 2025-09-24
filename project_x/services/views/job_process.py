from . import BookArtisan, ArtisanBookingSerializers, User
from rest_framework import viewsets, response, status, filters
from rest_framework.decorators import api_view
from django.shortcuts import get_list_or_404, get_object_or_404
from django.db.models import Q
from setup.processors.filters import BookingScheduleFilter
from django.db import transaction
from datetime import datetime
from rest_framework.response import Response
from main.models import Notification
import logging

logger = logging.getLogger('services')

class BookArtisanViewSet(viewsets.ModelViewSet):
    queryset = BookArtisan.objects.select_related('client', 'artisan').prefetch_related('services')
    serializer_class = ArtisanBookingSerializers

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            booking = serializer.save()
            # Notify client
            Notification.objects.create(
                recipient=booking.client,
                message=f"Your booking for {', '.join(service.name for service in booking.services.all())} has been created and is pending.",
                notification_type='BOOKING'
            )
            # Notify artisan (if specified)
            if booking.artisan:
                Notification.objects.create(
                    recipient=booking.artisan,
                    message=f"You have a new booking request from {booking.client.first_name} {booking.client.last_name} for {', '.join(service.name for service in booking.services.all())}.",
                    notification_type='BOOKING'
                )
            logger.info(f"Booking created: ID {booking.id} by client {booking.client.email} for artisan {booking.artisan.email if booking.artisan else 'None'}")
            return response.Response(serializer.data, status=status.HTTP_201_CREATED)
        logger.warning(f"Invalid booking data: {serializer.errors}")
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, *args, **kwargs):
        try:
            booking = self.get_object()
        except BookArtisan.DoesNotExist:
            logger.error(f"Booking {kwargs.get('pk')} not found for user {request.user.email}")
            return response.Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = self.get_serializer(booking, data=request.data, partial=True)
        if serializer.is_valid():
            booking = serializer.save()
            # Notify based on status change
            if booking.status == "Accepted":
                Notification.objects.create(
                    recipient=booking.client,
                    message=f"Your booking for {', '.join(service.name for service in booking.services.all())} has been accepted by {booking.artisan.first_name} {booking.artisan.last_name}.",
                    notification_type='BOOKING'
                )
                logger.info(f"Booking {booking.id} accepted by artisan {booking.artisan.email}")
            elif booking.status == "Declined":
                Notification.objects.create(
                    recipient=booking.client,
                    message=f"Your booking for {', '.join(service.name for service in booking.services.all())} was declined by {booking.artisan.first_name} {booking.artisan.last_name}.",
                    notification_type='BOOKING'
                )
                logger.info(f"Booking {booking.id} declined by artisan {booking.artisan.email}")
            return response.Response(serializer.data, status=status.HTTP_200_OK)
        logger.warning(f"Invalid booking update data: {serializer.errors}")
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def get_my_bookings(request):
    user = request.user
    try:
        bookings = BookArtisan.objects.filter(Q(client=user) | Q(artisan=user))
        serialized_data = ArtisanBookingSerializers(bookings, many=True).data
        logger.info(f"Bookings retrieved for user {user.email}")
    except BookArtisan.DoesNotExist:
        logger.warning(f"No bookings found for user {user.email}")
        return response.Response({"message": "No bookings found."}, status=status.HTTP_404_NOT_FOUND)    
    return response.Response(serialized_data, status=status.HTTP_200_OK)

class MyFilteredBookings(viewsets.ModelViewSet):
    serializer_class = ArtisanBookingSerializers

    def get_queryset(self):
        user = self.request.user
        try:
            bookings = BookArtisan.objects.filter(Q(status="Pending"), (Q(client=user) | Q(artisan=user))).select_related("client", "artisan").prefetch_related("services")
            logger.info(f"Pending bookings retrieved for user {user.email}")
        except BookArtisan.DoesNotExist:
            logger.warning(f"No pending bookings found for user {user.email}")
            return None
        return bookings
    
    def retrieve(self, request):
        user = request.user
        date_today = datetime.today().date()
        booking_id = request.query_params.get("booking_id")
        schedule_filter = request.query_params.get("schedule", None)

        try:
            if schedule_filter is not None and schedule_filter.lower() == "today":
                bookings = BookArtisan.objects.filter(
                    Q(client=user) | Q(artisan=user), scheduled_date=date_today
                )
                if bookings.exists():
                    serialized_data = self.get_serializer(bookings, many=True).data
                    logger.info(f"Today's bookings retrieved for user {user.email}")
                    return Response(serialized_data, status=status.HTTP_200_OK)
                logger.warning(f"No bookings scheduled for today for user {user.email}")
                return response.Response(
                    {"message": "No bookings scheduled for today."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if booking_id:
                booking = BookArtisan.objects.filter(artisan=user, id=booking_id).first()
                if booking:
                    serialized_data = self.get_serializer(booking).data
                    logger.info(f"Booking {booking_id} retrieved for user {user.email}")
                    return Response(serialized_data, status=status.HTTP_200_OK)
                logger.warning(f"No booking found with ID {booking_id} for user {user.email}")
                return response.Response(
                    {"message": "No booking found with the provided ID."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            logger.warning(f"Invalid parameters for booking retrieval by user {user.email}")
            return response.Response(
                {"message": "Please provide a valid schedule or booking_id parameter."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"Error retrieving bookings for user {user.email}: {str(e)}")
            return response.Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

@api_view(['POST'])
def toggle_availability(request):
    user = request.user
    availability = request.data.get("isAvailable")

    if availability is None:
        logger.warning(f"Availability toggle failed for user {user.email}: Missing 'isAvailable' field")
        return response.Response({"error": "The 'isAvailable' field is required."}, status=status.HTTP_400_BAD_REQUEST)

    if not isinstance(availability, bool):
        logger.warning(f"Availability toggle failed for user {user.email}: 'isAvailable' must be boolean")
        return response.Response({"error": "The 'isAvailable' field must be a boolean (true or false)."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        artisan = get_object_or_404(User.objects.select_for_update(), id=user.id)
        artisan.isAvailable = availability
        artisan.save()
        Notification.objects.create(
            recipient=artisan,
            message=f"Your availability has been set to {'available' if availability else 'unavailable'}.",
            notification_type='AVAILABILITY'
        )
        logger.info(f"Availability toggled to {availability} for artisan {user.email}")
    return response.Response({"message": "Availability toggled successfully!"}, status=status.HTTP_200_OK)

@api_view(['GET'])
def get_today_bookings(request):
    user = request.user
    date_today = datetime.today().date()
    booking_id = request.query_params.get("booking_id")
    schedule_filter = request.query_params.get("schedule", None)

    try:
        if schedule_filter is not None and schedule_filter.lower() == "today":
            bookings = BookArtisan.objects.filter(
                Q(client=user) | Q(artisan=user), scheduled_date=date_today
            )
            if bookings.exists():
                serialized_data = ArtisanBookingSerializers(bookings, many=True).data
                logger.info(f"Today's bookings retrieved for user {user.email}")
                return Response(serialized_data, status=status.HTTP_200_OK)
            logger.warning(f"No bookings scheduled for today for user {user.email}")
            return Response(
                {"message": "No bookings scheduled for today."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if booking_id:
            booking = BookArtisan.objects.filter(artisan=user, id=booking_id).first()
            if booking:
                serialized_data = ArtisanBookingSerializers(booking).data
                logger.info(f"Booking {booking_id} retrieved for user {user.email}")
                return Response(serialized_data, status=status.HTTP_200_OK)
            logger.warning(f"No booking found with ID {booking_id} for user {user.email}")
            return Response(
                {"message": "No booking found with the provided ID."},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.warning(f"Invalid parameters for booking retrieval by user {user.email}")
        return Response(
            {"message": "Please provide a valid schedule or booking_id parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.error(f"Error retrieving today's bookings for user {user.email}: {str(e)}")
        return Response(
            {"error": f"An unexpected error occurred: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )