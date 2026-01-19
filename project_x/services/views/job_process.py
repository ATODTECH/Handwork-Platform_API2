from . import BookArtisan, ArtisanBookingSerializers, User
from services.models.job_models import MaterialQuote
from services.serializers.job_serializers import MaterialQuoteSerializer
from rest_framework import viewsets, response, status, filters
from rest_framework.decorators import api_view
from django.shortcuts import get_list_or_404, get_object_or_404
from django.db.models import Q
from setup.processors.filters import BookingScheduleFilter
from django.conf import settings
from django.db import transaction
from datetime import datetime
from django.utils import timezone
from decimal import Decimal
from rest_framework.response import Response
from main.models import Notification
import logging
from paystackapi.paystack import Paystack
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import action, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.http import HttpResponse
import json
import hmac
import hashlib

logger = logging.getLogger('services')

paystack = Paystack(secret_key=settings.PAYSTACK_SECRET_KEY)

class BookArtisanViewSet(viewsets.ModelViewSet):
    queryset = BookArtisan.objects.select_related('client', 'artisan').prefetch_related('services')
    serializer_class = ArtisanBookingSerializers

    def get_queryset(self):
        user = self.request.user
        if user.role == 'ARTISAN':
            return BookArtisan.objects.filter(artisan=user)
        elif user.role == 'USER':
            return BookArtisan.objects.filter(client=user)
        return BookArtisan.objects.none()

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
        booking = self.get_object()

        # Only artisan can update status
        if request.user != booking.artisan:
            return response.Response(
                {"detail": "Only the artisan can update job status."},
                status=status.HTTP_403_FORBIDDEN
            )

        allowed_fields = {'status', 'agreed_amount'}
        if not set(request.data.keys()).issubset(allowed_fields):
            return response.Response(
                {"detail": "Invalid fields."}, status=status.HTTP_400_BAD_REQUEST
            )

        old_status = booking.status
        serializer = self.get_serializer(booking, data=request.data, partial=True)
        if not serializer.is_valid():
            return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        updated_booking = serializer.save()

        new_status = updated_booking.status

        if new_status == "Accepted" and old_status == "Pending":
            if not updated_booking.agreed_amount:
                return response.Response(
                    {"detail": "agreed_amount is required when accepting."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            Notification.objects.create(
                recipient=updated_booking.client,
                message=f"Your booking has been accepted by {booking.artisan.first_name}. "
                        f"Total: ₦{updated_booking.agreed_amount:,}. "
                        f"Please pay deposit of ₦{updated_booking.deposit_amount:,} to proceed.",
                notification_type='PAYMENT'
            )

        elif new_status == "In Progress" and old_status == "Deposit Paid":
            Notification.objects.create(
                recipient=updated_booking.client,
                message=f"The artisan has started working on your job.",
                notification_type='BOOKING'
            )

        elif new_status == "Completed" and old_status == "In Progress":
            Notification.objects.create(
                recipient=updated_booking.client,
                message=f"The job has been marked as completed by the artisan. "
                        f"Please pay the balance of ₦{updated_booking.balance_amount:,} to release funds.",
                notification_type='PAYMENT'
            )

        return response.Response(serializer.data, status=status.HTTP_200_OK)

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
    
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pay_deposit(request, pk):
    logger.info("🔥 pay_deposit HIT")

    booking = get_object_or_404(
        BookArtisan,
        id=pk,
        client=request.user
    )

    if booking.status != "Accepted":
        return Response(
            {"detail": "Booking must be accepted before paying deposit."},
            status=status.HTTP_400_BAD_REQUEST
        )

    if booking.agreed_amount is None or booking.agreed_amount <= 0:
        return Response(
            {"detail": "Agreed amount has not been set for this booking."},
            status=status.HTTP_400_BAD_REQUEST
        )

    reference = f"deposit_{booking.id}_{int(timezone.now().timestamp())}"

    try:
        response = paystack.transaction.initialize(
            reference=reference,
            amount=int(booking.agreed_amount * 100),
            email=request.user.email,
            callback_url="https://atodapi.up.railway.app/api/paystack/webhook/",
            metadata={
                "booking_id": booking.id,
                "payment_type": "deposit",
                "user_id": str(request.user.id)
            }
        )

        data = response["data"]

        return Response({
            "authorization_url": data["authorization_url"],
            "reference": data["reference"],
            "amount": booking.deposit_amount
        })

    except Exception as e:
        logger.exception("❌ Deposit payment failed")
        return Response(
            {"detail": "Unable to initialize deposit payment."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pay_balance(request, pk):
    booking = get_object_or_404(
        BookArtisan,
        id=pk,
        client=request.user
    )

    if booking.status != "Completed":
        return Response(
            {"detail": "Job must be marked as completed before paying balance."},
            status=status.HTTP_400_BAD_REQUEST
        )

    if not booking.balance_amount or booking.balance_amount <= 0:
        return Response(
            {"detail": "No balance due."},
            status=status.HTTP_400_BAD_REQUEST
        )

    reference = f"balance_{booking.id}_{int(timezone.now().timestamp())}"

    try:
        response = paystack.transaction.initialize(
            reference=reference,
            amount=int(booking.balance_amount * 100),
            email=request.user.email,
            callback_url="https://frontend.com/payment-success",
            metadata={
                "booking_id": booking.id,
                "payment_type": "balance",
                "user_id": request.user.id
            }
        )

        return Response({
            "authorization_url": response["data"]["authorization_url"],
            "reference": response["data"]["reference"],
            "amount": booking.balance_amount
        })

    except Exception as e:
        return Response(
            {"detail": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@csrf_exempt
@api_view(['POST'])
@permission_classes([AllowAny])
def paystack_webhook(request):
    # Verify webhook signature (highly recommended)
    signature = request.headers.get('x-paystack-signature')
    if settings.PAYSTACK_WEBHOOK_SECRET:
        computed_hmac = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(),
            request.body,
            hashlib.sha512
        ).hexdigest()
        if not hmac.compare_digest(computed_hmac, signature or ''):
            return HttpResponse(status=400)

    payload = json.loads(request.body)
    event = payload.get('event')

    if event == 'charge.success':
        data = payload['data']
        reference = data['reference']
        metadata = data.get('metadata', {})
        booking_id = metadata.get('booking_id')
        payment_type = metadata.get('payment_type')

        try:
            booking = BookArtisan.objects.get(id=booking_id)
        except BookArtisan.DoesNotExist:
            return HttpResponse(status=200)

        # Prevent double processing
        if payment_type == "deposit" and booking.status == "Accepted":
            booking.status = "Deposit Paid"
            booking.deposit_paid_at = timezone.now()
            booking.save()

            Notification.objects.create(
                recipient=booking.artisan,
                message=f"Client has paid ₦{booking.deposit_amount:,} deposit. You can now start the job!",
                notification_type='PAYMENT'
            )
            Notification.objects.create(
                recipient=booking.client,
                message="Deposit payment successful! The artisan will begin work soon.",
                notification_type='PAYMENT'
            )

        elif payment_type == "balance" and booking.status == "Completed":
            booking.status = "Paid in Full"
            booking.fully_paid_at = timezone.now()
            booking.save()

            Notification.objects.create(
                recipient=booking.artisan,
                message=f"Final payment of ₦{booking.balance_amount:,} received. Funds released!",
                notification_type='PAYMENT'
            )
            Notification.objects.create(
                recipient=booking.client,
                message="Balance payment successful. Thank you!",
                notification_type='PAYMENT'
            )

    return HttpResponse(status=200)

class MaterialQuoteViewSet(viewsets.ModelViewSet):
    queryset = MaterialQuote.objects.all()
    serializer_class = MaterialQuoteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'ARTISAN':
            return MaterialQuote.objects.filter(artisan=user)
        # Optionally allow admin/company to see all
        elif user.is_staff or user.role == 'ADMIN': 
            return MaterialQuote.objects.all()
        return MaterialQuote.objects.none()

    def perform_create(self, serializer):
        quote = serializer.save(artisan=self.request.user)

        # Notify client
        Notification.objects.create(
            recipient=quote.booking.client,
            message=f"The artisan has requested ₦{quote.total_cost:,} for materials on your job: {quote.job_title}.",
            notification_type='MATERIAL_REQUEST'
        )

    @action(detail=True, methods=['get'], url_path='my-requests')
    def my_requests(self, request, booking_id=None):
        """Extra endpoint: Get all material requests for a specific booking (useful for client)"""
        quotes = MaterialQuote.objects.filter(booking_id=booking_id)
        serializer = self.get_serializer(quotes, many=True)
        return Response(serializer.data)