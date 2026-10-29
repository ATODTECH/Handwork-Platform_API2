from . import BookArtisan, ArtisanBookingSerializers, User
from services.models.job_models import MaterialQuote, Transaction
from services.serializers.job_serializers import MaterialQuoteSerializer, CancelBookingSerializer, ReportProblemSerializer
from rest_framework import viewsets, response, status, filters
from rest_framework.decorators import api_view
from django.shortcuts import get_list_or_404, get_object_or_404
from django.db.models import Q
from setup.processors.filters import BookingScheduleFilter
from django.conf import settings
from django.db import transaction
from datetime import datetime, timedelta
from django.utils import timezone
from decimal import Decimal, InvalidOperation
from rest_framework.response import Response
from main.models import Notification
import logging, requests
from paystackapi.paystack import Paystack
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import action, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.http import HttpResponse
from rest_framework.views import APIView
from django.utils.dateparse import parse_date
import json
import hmac
import hashlib
import traceback

logger = logging.getLogger('services')

paystack = Paystack(secret_key=settings.PAYSTACK_SECRET_KEY)


# ─────────────────────────────────────────────
# BOOK ARTISAN VIEWSET
# ─────────────────────────────────────────────
# The only status changes an artisan may make; payments and the customer's confirmation move the others
ARTISAN_TRANSITIONS = {
    ("Pending", "Accepted"),
    ("Pending", "Declined"),
    ("Paid", "In Progress"),
    ("Deposit Paid", "In Progress"),
    ("In Progress", "Completed"),
}


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
        if not request.user.is_authenticated or request.user.role != 'USER':
            return response.Response(
                {"detail": "Only customers can book artisans."}, status=status.HTTP_403_FORBIDDEN
            )
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            booking = serializer.save(client=request.user)
            Notification.objects.create(
                recipient=booking.client,
                message=f"Your booking for {', '.join(service.name for service in booking.services.all())} has been created and is pending.",
                notification_type='BOOKING'
            )
            if booking.artisan:
                Notification.objects.create(
                    recipient=booking.artisan,
                    message=f"You have a new booking request from {booking.client.first_name} {booking.client.last_name} for {', '.join(service.name for service in booking.services.all())}.",
                    notification_type='BOOKING'
                )
            logger.info(f"Booking created: ID {booking.id} by client {booking.client.email}")
            return response.Response(serializer.data, status=status.HTTP_201_CREATED)
        logger.warning(f"Invalid booking data: {serializer.errors}")
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, *args, **kwargs):
        booking = self.get_object()

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
        new_status = request.data.get('status', old_status)
        if (old_status, new_status) not in ARTISAN_TRANSITIONS:
            return response.Response(
                {"detail": f"A booking that is {old_status} can't be changed to {new_status}."},
                status=status.HTTP_400_BAD_REQUEST
            )
        # The price is agreed once, when accepting
        if new_status == "Accepted":
            try:
                agreed = Decimal(str(request.data.get('agreed_amount') or 0))
            except InvalidOperation:
                agreed = Decimal(0)
            if agreed <= 0:
                return response.Response(
                    {"agreed_amount": ["Enter the price you agree to do the job for."]},
                    status=status.HTTP_400_BAD_REQUEST
                )
        elif 'agreed_amount' in request.data:
            return response.Response(
                {"detail": "The price can only be set when accepting."}, status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(booking, data=request.data, partial=True)
        if not serializer.is_valid():
            return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        updated_booking = serializer.save()
        if new_status == "Completed":
            updated_booking.completed_at = timezone.now()
            updated_booking.save(update_fields=["completed_at"])

        if new_status == "Accepted":
            above_budget = (f" (your budget was ₦{updated_booking.budget:,})"
                            if updated_booking.agreed_amount > updated_booking.budget else "")
            Notification.objects.create(
                recipient=updated_booking.client,
                message=f"{booking.artisan.first_name} accepted your booking for ₦{updated_booking.agreed_amount:,}{above_budget}. "
                        f"Pay to confirm it, or cancel if the price doesn't work for you.",
                notification_type='PAYMENT'
            )
        elif new_status == "Declined":
            Notification.objects.create(
                recipient=updated_booking.client,
                message=f"{booking.artisan.first_name} can't take your booking. You can book another artisan.",
                notification_type='BOOKING'
            )
        elif new_status == "In Progress":
            Notification.objects.create(
                recipient=updated_booking.client,
                message="The artisan has started working on your job.",
                notification_type='BOOKING'
            )
        elif new_status == "Completed" and updated_booking.deposit_paid_at:
            Notification.objects.create(
                recipient=updated_booking.client,
                message=f"The job has been marked as completed by the artisan. "
                        f"Please pay the balance of ₦{updated_booking.balance_amount:,} to release funds.",
                notification_type='PAYMENT'
            )
        elif new_status == "Completed":
            Notification.objects.create(
                recipient=updated_booking.client,
                message=f"{booking.artisan.first_name} has marked your job as done. Please confirm it so we can pay them. "
                        f"If there's a problem, contact support within {RELEASE_AFTER_HOURS} hours.",
                notification_type='BOOKING'
            )

        return response.Response(serializer.data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────
# GET ALL MY BOOKINGS (client + artisan)
# ─────────────────────────────────────────────
@api_view(['GET'])
def get_my_bookings(request):
    user = request.user
    bookings = BookArtisan.objects.filter(Q(client=user) | Q(artisan=user))
    if not bookings.exists():
        return response.Response({"message": "No bookings found."}, status=status.HTTP_404_NOT_FOUND)
    serialized_data = ArtisanBookingSerializers(bookings, many=True, context={"request": request}).data
    logger.info(f"Bookings retrieved for user {user.email}")
    return response.Response(serialized_data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────
# FILTERED BOOKINGS VIEWSET (Pending only)
# ─────────────────────────────────────────────
class MyFilteredBookings(viewsets.ModelViewSet):
    serializer_class = ArtisanBookingSerializers

    def get_queryset(self):
        user = self.request.user
        bookings = BookArtisan.objects.filter(
            Q(status="Pending"),
            Q(client=user) | Q(artisan=user)
        ).select_related("client", "artisan").prefetch_related("services")
        logger.info(f"Pending bookings retrieved for user {user.email}")
        return bookings

    # ✅ FIXED — added pk=None (required by ModelViewSet router)
    def retrieve(self, request, pk=None):
        user = request.user
        date_today = datetime.today().date()
        booking_id = request.query_params.get("booking_id")
        schedule_filter = request.query_params.get("schedule", None)

        try:
            if schedule_filter is not None and schedule_filter.lower() == "today":
                bookings = BookArtisan.objects.filter(
                    Q(client=user) | Q(artisan=user),
                    scheduled_date=date_today
                )
                if bookings.exists():
                    serialized_data = self.get_serializer(bookings, many=True).data
                    logger.info(f"Today's bookings retrieved for user {user.email}")
                    return Response(serialized_data, status=status.HTTP_200_OK)
                logger.warning(f"No bookings scheduled for today for user {user.email}")
                return Response(
                    {"message": "No bookings scheduled for today."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if booking_id:
                booking = BookArtisan.objects.filter(
                    Q(client=user) | Q(artisan=user),
                    id=booking_id
                ).first()
                if booking:
                    serialized_data = self.get_serializer(booking).data
                    logger.info(f"Booking {booking_id} retrieved for user {user.email}")
                    return Response(serialized_data, status=status.HTTP_200_OK)
                logger.warning(f"No booking found with ID {booking_id} for user {user.email}")
                return Response(
                    {"message": "No booking found with the provided ID."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # fallback — return the pk-based booking
            if pk:
                booking = BookArtisan.objects.filter(
                    Q(client=user) | Q(artisan=user),
                    id=pk
                ).first()
                if booking:
                    return Response(self.get_serializer(booking).data, status=status.HTTP_200_OK)

            return Response(
                {"message": "Please provide a valid schedule or booking_id parameter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            logger.error(f"Error retrieving bookings for user {user.email}: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ─────────────────────────────────────────────
# TOGGLE AVAILABILITY
# ─────────────────────────────────────────────
@api_view(['POST'])
def toggle_availability(request):
    user = request.user
    availability = request.data.get("isAvailable")

    if availability is None:
        return response.Response(
            {"error": "The 'isAvailable' field is required."},
            status=status.HTTP_400_BAD_REQUEST
        )
    if not isinstance(availability, bool):
        return response.Response(
            {"error": "The 'isAvailable' field must be a boolean (true or false)."},
            status=status.HTTP_400_BAD_REQUEST
        )

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


# ─────────────────────────────────────────────
# GET TODAY'S BOOKINGS
# ─────────────────────────────────────────────
@api_view(['GET'])
def get_today_bookings(request):
    user = request.user
    date_today = datetime.today().date()
    booking_id = request.query_params.get("booking_id")
    schedule_filter = request.query_params.get("schedule", None)

    try:
        if schedule_filter is not None and schedule_filter.lower() == "today":
            bookings = BookArtisan.objects.filter(
                Q(client=user) | Q(artisan=user),
                scheduled_date=date_today
            )
            if bookings.exists():
                serialized_data = ArtisanBookingSerializers(bookings, many=True).data
                logger.info(f"Today's bookings retrieved for user {user.email}")
                return Response(serialized_data, status=status.HTTP_200_OK)
            return Response(
                {"message": "No bookings scheduled for today."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if booking_id:
            booking = BookArtisan.objects.filter(
                Q(client=user) | Q(artisan=user),
                id=booking_id
            ).first()
            if booking:
                serialized_data = ArtisanBookingSerializers(booking).data
                logger.info(f"Booking {booking_id} retrieved for user {user.email}")
                return Response(serialized_data, status=status.HTTP_200_OK)
            return Response(
                {"message": "No booking found with the provided ID."},
                status=status.HTTP_404_NOT_FOUND,
            )

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


# ─────────────────────────────────────────────
# PAYMENTS
# ─────────────────────────────────────────────
# Customers pay the full agreed price once (`pay_booking`). Handwork Expert holds it until the customer
# confirms the job is done (`confirm_booking`), or RELEASE_AFTER_HOURS after the artisan marks it done
# (`manage.py release_completed_bookings`). Then the artisan's wallet is credited, minus PLATFORM_FEE.
# Bookings from before this paid a 70% deposit and a 30% balance; `pay_deposit` and `pay_balance`
# stay for those and for older app builds.

PLATFORM_FEE = Decimal('0.10')
RELEASE_AFTER_HOURS = 48
# Before paying, a customer may cancel (which also turns down the artisan's price)
CANCELLABLE_STATUSES = ("Pending", "Accepted")
# Once paid and until the artisan is paid, a customer may report a problem
DISPUTABLE_STATUSES = ("Paid", "In Progress", "Completed")
PAYSTACK_TIMEOUT_SECONDS = 15


def start_paystack_payment(booking, user, payment_type, amount):
    """Opens a Paystack transaction; the reference encodes what it pays for: {type}_{booking id}_{time}"""
    reference = f"{payment_type}_{booking.id}_{int(timezone.now().timestamp())}"
    paystack_response = paystack.transaction.initialize(
        reference=reference,
        amount=int(amount * 100),
        email=user.email,
        callback_url=f"{settings.BASE_URL}/api/paystack/webhook/",
        metadata={"booking_id": booking.id, "payment_type": payment_type, "user_id": str(user.id)},
    )
    if not paystack_response.get("status"):
        return Response(
            {"detail": paystack_response.get("message", "Payment initialization failed")},
            status=status.HTTP_400_BAD_REQUEST
        )
    data = paystack_response["data"]
    return Response({
        "authorization_url": data["authorization_url"],
        # The web resumes this transaction in Paystack's pop-up
        "access_code": data["access_code"],
        "reference": data["reference"],
        "amount": amount,
    })


def verify_paystack(reference):
    """The charge Paystack reports for this reference, or None when it isn't a successful one"""
    resp = requests.get(
        f"https://api.paystack.co/transaction/verify/{reference}",
        headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        timeout=PAYSTACK_TIMEOUT_SECONDS,
    )
    data = resp.json()
    if data.get("status") and data.get("data", {}).get("status") == "success":
        return data["data"]
    return None


def artisan_share(gross):
    """What the artisan earns from a job: the price minus our fee (no VAT is taken from it)"""
    gross = to_decimal(gross)
    return (gross - gross * PLATFORM_FEE).quantize(Decimal('0.01'))


def credit_artisan(booking):
    """Adds the artisan's share of a fully paid job to their wallet and records the earning"""
    gross = to_decimal(booking.agreed_amount or booking.budget)
    net_amount = artisan_share(gross)
    platform_fee = gross - net_amount
    vat = Decimal('0.00')

    artisan = User.objects.select_for_update().get(pk=booking.artisan_id)
    artisan.wallet_balance = (artisan.wallet_balance + net_amount).quantize(Decimal('0.01'))
    artisan.save(update_fields=['wallet_balance'])
    Transaction.objects.create(
        artisan=artisan,
        booking=booking,
        gross_amount=gross,
        platform_fee=platform_fee,
        vat=vat,
        net_amount=net_amount,
        transaction_type='EARNING'
    )
    booking.released_at = timezone.now()
    return net_amount


def release_booking(booking, *, automatic=False, client_message=None):
    """Pays the artisan for a job the customer confirmed (or didn't dispute in time). Call inside a transaction."""
    net_amount = credit_artisan(booking)
    booking.status = "Released"
    booking.save()
    Notification.objects.create(
        recipient=booking.artisan,
        message=f"₦{net_amount:,} for your job with {booking.client.first_name} has been added to your earnings.",
        notification_type="PAYMENT"
    )
    if client_message is None:
        client_message = (
            f"We've paid {booking.artisan.first_name} for your job, as {RELEASE_AFTER_HOURS} hours passed without a problem reported."
            if automatic else f"Thanks for confirming. We've paid {booking.artisan.first_name} for your job."
        )
    Notification.objects.create(recipient=booking.client, message=client_message, notification_type="PAYMENT")


def record_payment(reference, amount_kobo):
    """
    Applies a successful Paystack charge to its booking. Safe to call more than once for the same
    charge: the webhook, the redirect callback and the web's verify call may all report it.
    """
    try:
        payment_type, booking_id = reference.split("_")[0], int(reference.split("_")[1])
    except (IndexError, ValueError):
        logger.error(f"Unrecognised payment reference: {reference}")
        return None

    with transaction.atomic():
        booking = BookArtisan.objects.select_for_update(of=("self",)).select_related("client", "artisan").filter(id=booking_id).first()
        if booking is None:
            logger.error(f"Payment {reference} is for a booking that doesn't exist")
            return None

        # What each payment pays for: the status it must find the booking in, and the amount due
        due = {
            "full": ("Accepted", booking.agreed_amount),
            "deposit": ("Accepted", booking.deposit_amount),
            "balance": ("Completed", booking.balance_amount if booking.deposit_paid_at else None),
        }.get(payment_type)
        if due is None or booking.status != due[0]:
            # Already applied, or not a payment this booking is waiting for
            return booking
        if due[1] is None or amount_kobo != int(due[1] * 100):
            logger.warning(f"Payment {reference}: expected {due[1]}, Paystack charged {amount_kobo} kobo")
            return booking

        now = timezone.now()
        if payment_type == "full":
            booking.status = "Paid"
            booking.fully_paid_at = now
            booking.payment_reference = reference
            artisan_message = (f"{booking.client.first_name} has paid ₦{booking.agreed_amount:,}. We're holding it until "
                               f"the job is done. You can start on the scheduled day.")
            client_message = "Payment received. We'll hold it and only pay the artisan once you confirm the job is done."
        elif payment_type == "deposit":
            booking.status = "Deposit Paid"
            booking.deposit_paid_at = now
            artisan_message = f"Client has paid ₦{booking.deposit_amount:,} deposit. You can now start the job!"
            client_message = "Deposit payment successful! The artisan will begin work soon."
        else:
            credit_artisan(booking)
            booking.status = "Paid in Full"
            booking.fully_paid_at = now
            artisan_message = f"Final payment of ₦{booking.balance_amount:,} received. Funds released!"
            client_message = "Balance payment successful. Thank you!"
        booking.save()

        Notification.objects.create(recipient=booking.artisan, message=artisan_message, notification_type="PAYMENT")
        Notification.objects.create(recipient=booking.client, message=client_message, notification_type="PAYMENT")
        logger.info(f"Payment {reference} applied: booking {booking.id} is now {booking.status}")
        return booking


# ─────────────────────────────────────────────
# PAY FOR A BOOKING (the full agreed price)
# ─────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pay_booking(request, pk):
    booking = get_object_or_404(BookArtisan, id=pk, client=request.user)
    if booking.status != "Accepted":
        return Response({"detail": "This booking isn't waiting for payment."}, status=status.HTTP_400_BAD_REQUEST)
    if not booking.agreed_amount or booking.agreed_amount <= 0:
        return Response({"detail": "Agreed amount not set."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        return start_paystack_payment(booking, request.user, "full", booking.agreed_amount)
    except Exception:
        logger.exception(f"Starting payment for booking {booking.id} failed")
        return Response({"detail": "We couldn't reach Paystack. Please try again."}, status=status.HTTP_502_BAD_GATEWAY)


# ─────────────────────────────────────────────
# VERIFY A PAYMENT (straight after Paystack's pop-up closes)
# ─────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_booking_payment(request, pk):
    booking = get_object_or_404(BookArtisan, id=pk, client=request.user)
    reference = str(request.data.get("reference", ""))
    if not reference.startswith(f"full_{booking.id}_"):
        return Response({"reference": ["This payment isn't for this booking."]}, status=status.HTTP_400_BAD_REQUEST)
    try:
        charge = verify_paystack(reference)
    except Exception:
        logger.exception(f"Verifying payment {reference} failed")
        return Response({"detail": "We couldn't reach Paystack. Please try again."}, status=status.HTTP_502_BAD_GATEWAY)
    if charge is None:
        return Response({"detail": "Paystack hasn't confirmed this payment."}, status=status.HTTP_400_BAD_REQUEST)
    record_payment(reference, charge.get("amount", 0))
    booking.refresh_from_db()
    return Response(ArtisanBookingSerializers(booking, context={"request": request}).data)


# ─────────────────────────────────────────────
# CONFIRM THE JOB IS DONE (pays the artisan)
# ─────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_booking(request, pk):
    with transaction.atomic():
        booking = get_object_or_404(
            BookArtisan.objects.select_for_update(of=("self",)).select_related("client", "artisan"), id=pk, client=request.user
        )
        # Old deposit bookings are paid out when the balance is paid instead
        if booking.status != "Completed" or booking.deposit_paid_at:
            return Response(
                {"detail": "Only a job the artisan has marked as done can be confirmed."},
                status=status.HTTP_400_BAD_REQUEST
            )
        release_booking(booking)
    return Response(ArtisanBookingSerializers(booking, context={"request": request}).data)


# ─────────────────────────────────────────────
# CANCEL A BOOKING (customer, before paying)
# ─────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_booking(request, pk):
    serializer = CancelBookingSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    reason = serializer.validated_data.get("reason", "")
    with transaction.atomic():
        booking = get_object_or_404(
            BookArtisan.objects.select_for_update(of=("self",)).select_related("client", "artisan"), id=pk, client=request.user
        )
        if booking.status not in CANCELLABLE_STATUSES:
            return Response(
                {"detail": "Only a booking you haven't paid for can be cancelled. Report a problem instead."},
                status=status.HTTP_400_BAD_REQUEST
            )
        declined_price = booking.status == "Accepted"
        booking.status = "Cancelled"
        booking.cancelled_at = timezone.now()
        booking.cancellation_reason = reason
        booking.save()
        if booking.artisan:
            what = f"declined your price of ₦{booking.agreed_amount:,} and cancelled" if declined_price else "cancelled"
            Notification.objects.create(
                recipient=booking.artisan,
                message=f"{booking.client.first_name} {what} their booking.{f' Reason: {reason}' if reason else ''}",
                notification_type='BOOKING'
            )
    logger.info(f"Booking {booking.id} cancelled by {request.user.email}")
    return Response(ArtisanBookingSerializers(booking, context={"request": request}).data)


# ─────────────────────────────────────────────
# REPORT A PROBLEM (customer, once paid; holds the payment for staff)
# ─────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def report_problem(request, pk):
    serializer = ReportProblemSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        booking = get_object_or_404(
            BookArtisan.objects.select_for_update(of=("self",)).select_related("client", "artisan"), id=pk, client=request.user
        )
        # Old deposit bookings are paid out with the balance, so there's nothing for us to hold
        if booking.status not in DISPUTABLE_STATUSES or booking.deposit_paid_at:
            return Response(
                {"detail": "You can report a problem once you've paid, until the artisan is paid."},
                status=status.HTTP_400_BAD_REQUEST
            )
        booking.status = "Disputed"
        booking.disputed_at = timezone.now()
        booking.dispute_reason = serializer.validated_data["reason"]
        booking.save()
        Notification.objects.create(
            recipient=booking.client,
            message="Thanks for letting us know. We're holding your payment and our team will contact you shortly.",
            notification_type='BOOKING'
        )
        if booking.artisan:
            Notification.objects.create(
                recipient=booking.artisan,
                message=f"{booking.client.first_name} reported a problem with their job. We're holding the payment "
                        f"while our team looks into it, and will contact you.",
                notification_type='BOOKING'
            )
    logger.info(f"Problem reported on booking {booking.id} by {request.user.email}")
    return Response(ArtisanBookingSerializers(booking, context={"request": request}).data)


# ─────────────────────────────────────────────
# PAY DEPOSIT / PAY BALANCE (old 70% + 30% bookings)
# ─────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pay_deposit(request, pk):
    booking = get_object_or_404(BookArtisan, id=pk, client=request.user)
    if booking.status != "Accepted":
        return Response(
            {"detail": "Booking must be accepted before paying deposit."},
            status=status.HTTP_400_BAD_REQUEST
        )
    if not booking.agreed_amount or booking.agreed_amount <= 0:
        return Response({"detail": "Agreed amount not set."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        booking.save()  # works out deposit_amount and balance_amount from the agreed price
        return start_paystack_payment(booking, request.user, "deposit", booking.deposit_amount)
    except Exception as e:
        logger.exception("Deposit payment initialization failed")
        return Response({"detail": f"Server error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pay_balance(request, pk):
    booking = get_object_or_404(BookArtisan, id=pk, client=request.user)
    # Bookings paid in full up front have no balance; only deposit bookings do
    if booking.status != "Completed" or not booking.deposit_paid_at:
        return Response(
            {"detail": "Job must be marked as completed before paying balance."},
            status=status.HTTP_400_BAD_REQUEST
        )
    if not booking.balance_amount or booking.balance_amount <= 0:
        return Response({"detail": "No balance due."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        return start_paystack_payment(booking, request.user, "balance", booking.balance_amount)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─────────────────────────────────────────────
# PAYSTACK WEBHOOK (POST) and redirect callback (GET, used by the mobile app's checkout)
# ─────────────────────────────────────────────
@api_view(['POST', 'GET'])
@permission_classes([AllowAny])
def paystack_webhook(request):
    logger.info(f"Paystack callback/webhook hit: {request.method}")

    if request.method == 'GET':
        reference = request.query_params.get('reference') or request.query_params.get('trxref')
        if not reference:
            return Response({"message": "No reference provided"}, status=400)
        try:
            charge = verify_paystack(reference)
        except Exception:
            logger.exception("Error verifying GET callback")
            return Response({"message": "Unable to verify payment status"}, status=500)
        if charge is None:
            return Response({"message": "Payment failed or abandoned.", "status": "failed"}, status=200)
        record_payment(reference, charge.get("amount", 0))
        return Response({"message": "Payment successful!", "status": "success"}, status=200)

    signature = request.headers.get('x-paystack-signature')
    if not signature:
        logger.warning("Missing Paystack signature in webhook")
        return Response(status=400)
    computed_signature = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode('utf-8'),
        request.body,
        hashlib.sha512
    ).hexdigest()
    if not hmac.compare_digest(computed_signature, signature):
        logger.warning("Invalid Paystack webhook signature")
        return Response(status=400)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook payload")
        return Response(status=400)

    event = payload.get('event', '')
    if event != 'charge.success' and not event.startswith('transfer.'):
        return Response({"message": "Event ignored"}, status=200)
    data = payload.get('data', {})
    if not data.get('reference'):
        return Response(status=400)
    try:
        if event == 'charge.success':
            record_payment(data['reference'], data.get('amount', 0))
        else:
            # Withdrawals to artisans' banks (imported here: payouts builds on this module)
            from services.views.payouts import record_transfer
            record_transfer(event, data)
    except Exception:
        # Answer with an error so Paystack retries; record_payment is safe to run again
        logger.exception(f"Error processing webhook for {data['reference']}")
        return Response(status=500)
    return Response({"message": "Webhook processed successfully"}, status=200)


# ─────────────────────────────────────────────
# MATERIAL QUOTE VIEWSET
# ─────────────────────────────────────────────
class MaterialQuoteViewSet(viewsets.ModelViewSet):
    queryset = MaterialQuote.objects.all()
    serializer_class = MaterialQuoteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'ARTISAN':
            return MaterialQuote.objects.filter(artisan=user)
        elif user.is_staff or user.role == 'ADMIN':
            return MaterialQuote.objects.all()
        return MaterialQuote.objects.none()

    def perform_create(self, serializer):
        quote = serializer.save(artisan=self.request.user)
        Notification.objects.create(
            recipient=quote.booking.client,
            message=f"The artisan has requested ₦{quote.total_cost:,} for materials on your job: {quote.job_title}.",
            notification_type='MATERIAL_REQUEST'
        )

    @action(detail=True, methods=['get'], url_path='my-requests')
    def my_requests(self, request, booking_id=None):
        quotes = MaterialQuote.objects.filter(booking_id=booking_id)
        serializer = self.get_serializer(quotes, many=True)
        return Response(serializer.data)


# ─────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────
def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal('0.00')
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


# ─────────────────────────────────────────────
# ARTISAN EARNINGS
# ─────────────────────────────────────────────
EARNED_STATUSES = ("Released", "Paid in Full")


class ArtisanEarningsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != 'ARTISAN':
            return Response({"detail": "Artisan only"}, status=403)

        artisan = request.user
        balance = artisan.wallet_balance
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        completed_jobs = BookArtisan.objects.filter(artisan=artisan, status__in=EARNED_STATUSES)

        today_earnings = sum(
            self.calculate_net(job.agreed_amount or job.budget)
            for job in completed_jobs
            if job.released_at and job.released_at.date() == now.date()
        )
        week_earnings = sum(
            self.calculate_net(job.agreed_amount or job.budget)
            for job in completed_jobs
            if job.released_at and job.released_at >= week_start
        )
        month_earnings = sum(
            self.calculate_net(job.agreed_amount or job.budget)
            for job in completed_jobs
            if job.released_at and job.released_at >= month_start
        )

        return Response({
            "balance": float(balance),
            "total_earnings": {
                "today": float(today_earnings),
                "this_week": float(week_earnings),
                "last_month": float(month_earnings)
            }
        })

    def calculate_net(self, gross):
        return artisan_share(gross)


# ─────────────────────────────────────────────
# ARTISAN PAYMENTS
# ─────────────────────────────────────────────
class ArtisanPaymentsView(APIView):
    permission_classes = [IsAuthenticated]

    def calculate_net(self, gross):
        return float(artisan_share(gross))

    def get(self, request):
        try:
            if request.user.role != 'ARTISAN':
                return Response({"detail": "Artisan only"}, status=403)

            recent = BookArtisan.objects.filter(
                artisan=request.user,
                status__in=EARNED_STATUSES
            ).select_related('client').prefetch_related('services').order_by('-released_at')[:10]

            recent_data = []
            for job in recent:
                client_photo = None
                if getattr(job.client, "photo", None):
                    try:
                        client_photo = job.client.photo.url
                    except Exception:
                        client_photo = None

                recent_data.append({
                    "client": {
                        "id": str(job.client.id),
                        "name": job.client.get_full_name() or job.client.email,
                        "photo": client_photo
                    },
                    "service": ", ".join(s.name for s in job.services.all()),
                    "location": job.address or "N/A",
                    "date": job.released_at.strftime("%d/%m/%Y") if job.released_at else "N/A",
                    "amount": self.calculate_net(job.agreed_amount or job.budget),
                    "status": "Paid"
                })

            pending = BookArtisan.objects.filter(
                artisan=request.user,
                status="Completed"
            ).select_related('client').prefetch_related('services').order_by('-updated_at')

            pending_data = []
            for job in pending:
                client_photo = None
                if getattr(job.client, "photo", None):
                    try:
                        client_photo = job.client.photo.url
                    except Exception:
                        client_photo = None

                pending_data.append({
                    "client": {
                        "id": str(job.client.id),
                        "name": job.client.get_full_name() or job.client.email,
                        "photo": client_photo
                    },
                    "service": ", ".join(s.name for s in job.services.all()),
                    "location": job.address or "N/A",
                    "date": job.scheduled_date.strftime("%d/%m/%Y") if job.scheduled_date else "N/A",
                    # Old deposit bookings still owe the balance; new ones are paid and await confirmation
                    "amount": (float(job.balance_amount or 0) if job.deposit_paid_at
                               else self.calculate_net(job.agreed_amount or job.budget)),
                    "status": "Pending"
                })

            return Response({
                "recent_payments": recent_data,
                "pending_payments": pending_data
            })

        except Exception as e:
            print("=== ArtisanPaymentsView CRASH ===", str(e))
            print(traceback.format_exc())
            raise


# ─────────────────────────────────────────────
# ARTISAN EARNINGS FILTER
# ─────────────────────────────────────────────
class ArtisanEarningsFilterView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != 'ARTISAN':
            return Response({"detail": "Artisan only"}, status=403)

        artisan = request.user
        balance = float(artisan.wallet_balance)

        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')

        start_date = parse_date(start_date_str) if start_date_str else None
        end_date = parse_date(end_date_str) if end_date_str else None

        completed_jobs = BookArtisan.objects.filter(artisan=artisan, status__in=EARNED_STATUSES)

        if start_date and end_date:
            end_date_next = end_date + timedelta(days=1)
            completed_jobs = completed_jobs.filter(
                released_at__gte=start_date,
                released_at__lt=end_date_next
            )
        elif start_date or end_date:
            return Response(
                {"detail": "Both start_date and end_date are required for filtering."},
                status=400
            )

        total_earnings = sum(
            self.calculate_net(job.agreed_amount or job.budget)
            for job in completed_jobs
            if job.released_at
        )

        return Response({
            "balance": balance,
            "total_earnings": float(total_earnings),
            "start_date": start_date_str,
            "end_date": end_date_str,
        })

    def calculate_net(self, gross):
        return float(artisan_share(gross))


# from . import BookArtisan, ArtisanBookingSerializers, User
# from services.models.job_models import MaterialQuote, Transaction
# from services.serializers.job_serializers import MaterialQuoteSerializer
# from rest_framework import viewsets, response, status, filters
# from rest_framework.decorators import api_view
# from django.shortcuts import get_list_or_404, get_object_or_404
# from django.db.models import Q
# from setup.processors.filters import BookingScheduleFilter
# from django.conf import settings
# from django.db import transaction
# from datetime import datetime, timedelta
# from django.utils import timezone
# from decimal import Decimal, InvalidOperation
# from rest_framework.response import Response
# from main.models import Notification
# import logging, requests
# from paystackapi.paystack import Paystack
# from django.views.decorators.csrf import csrf_exempt
# from rest_framework.decorators import action, permission_classes
# from rest_framework.permissions import IsAuthenticated, AllowAny
# from django.http import HttpResponse
# from rest_framework.views import APIView
# from django.utils.dateparse import parse_date
# import json
# import hmac
# import hashlib
# import traceback

# logger = logging.getLogger('services')

# paystack = Paystack(secret_key=settings.PAYSTACK_SECRET_KEY)

# class BookArtisanViewSet(viewsets.ModelViewSet):
#     queryset = BookArtisan.objects.select_related('client', 'artisan').prefetch_related('services')
#     serializer_class = ArtisanBookingSerializers

#     def get_queryset(self):
#         user = self.request.user
#         if user.role == 'ARTISAN':
#             return BookArtisan.objects.filter(artisan=user)
#         elif user.role == 'USER':
#             return BookArtisan.objects.filter(client=user)
#         return BookArtisan.objects.none()

#     def create(self, request, *args, **kwargs):
#         serializer = self.get_serializer(data=request.data)
#         if serializer.is_valid():
#             booking = serializer.save()
#             # Notify client
#             Notification.objects.create(
#                 recipient=booking.client,
#                 message=f"Your booking for {', '.join(service.name for service in booking.services.all())} has been created and is pending.",
#                 notification_type='BOOKING'
#             )
#             # Notify artisan (if specified)
#             if booking.artisan:
#                 Notification.objects.create(
#                     recipient=booking.artisan,
#                     message=f"You have a new booking request from {booking.client.first_name} {booking.client.last_name} for {', '.join(service.name for service in booking.services.all())}.",
#                     notification_type='BOOKING'
#                 )
#             logger.info(f"Booking created: ID {booking.id} by client {booking.client.email} for artisan {booking.artisan.email if booking.artisan else 'None'}")
#             return response.Response(serializer.data, status=status.HTTP_201_CREATED)
#         logger.warning(f"Invalid booking data: {serializer.errors}")
#         return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#     def update(self, request, *args, **kwargs):
#         booking = self.get_object()

#         # Only artisan can update status
#         if request.user != booking.artisan:
#             return response.Response(
#                 {"detail": "Only the artisan can update job status."},
#                 status=status.HTTP_403_FORBIDDEN
#             )

#         allowed_fields = {'status', 'agreed_amount'}
#         if not set(request.data.keys()).issubset(allowed_fields):
#             return response.Response(
#                 {"detail": "Invalid fields."}, status=status.HTTP_400_BAD_REQUEST
#             )

#         old_status = booking.status
#         serializer = self.get_serializer(booking, data=request.data, partial=True)
#         if not serializer.is_valid():
#             return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#         updated_booking = serializer.save()

#         new_status = updated_booking.status

#         if new_status == "Accepted" and old_status == "Pending":
#             if not updated_booking.agreed_amount:
#                 return response.Response(
#                     {"detail": "agreed_amount is required when accepting."},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )

#             Notification.objects.create(
#                 recipient=updated_booking.client,
#                 message=f"Your booking has been accepted by {booking.artisan.first_name}. "
#                         f"Total: ₦{updated_booking.agreed_amount:,}. "
#                         f"Please pay deposit of ₦{updated_booking.deposit_amount:,} to proceed.",
#                 notification_type='PAYMENT'
#             )

#         elif new_status == "In Progress" and old_status == "Deposit Paid":
#             Notification.objects.create(
#                 recipient=updated_booking.client,
#                 message=f"The artisan has started working on your job.",
#                 notification_type='BOOKING'
#             )

#         elif new_status == "Completed" and old_status == "In Progress":
#             Notification.objects.create(
#                 recipient=updated_booking.client,
#                 message=f"The job has been marked as completed by the artisan. "
#                         f"Please pay the balance of ₦{updated_booking.balance_amount:,} to release funds.",
#                 notification_type='PAYMENT'
#             )

#         return response.Response(serializer.data, status=status.HTTP_200_OK)

# @api_view(['GET'])
# def get_my_bookings(request):
#     user = request.user
#     try:
#         bookings = BookArtisan.objects.filter(Q(client=user) | Q(artisan=user))
#         serialized_data = ArtisanBookingSerializers(bookings, many=True).data
#         logger.info(f"Bookings retrieved for user {user.email}")
#     except BookArtisan.DoesNotExist:
#         logger.warning(f"No bookings found for user {user.email}")
#         return response.Response({"message": "No bookings found."}, status=status.HTTP_404_NOT_FOUND)    
#     return response.Response(serialized_data, status=status.HTTP_200_OK)

# class MyFilteredBookings(viewsets.ModelViewSet):
#     serializer_class = ArtisanBookingSerializers

#     def get_queryset(self):
#         user = self.request.user
#         try:
#             bookings = BookArtisan.objects.filter(Q(status="Pending"), (Q(client=user) | Q(artisan=user))).select_related("client", "artisan").prefetch_related("services")
#             logger.info(f"Pending bookings retrieved for user {user.email}")
#         except BookArtisan.DoesNotExist:
#             logger.warning(f"No pending bookings found for user {user.email}")
#             return None
#         return bookings
    
#     def retrieve(self, request):
#         user = request.user
#         date_today = datetime.today().date()
#         booking_id = request.query_params.get("booking_id")
#         schedule_filter = request.query_params.get("schedule", None)

#         try:
#             if schedule_filter is not None and schedule_filter.lower() == "today":
#                 bookings = BookArtisan.objects.filter(
#                     Q(client=user) | Q(artisan=user), scheduled_date=date_today
#                 )
#                 if bookings.exists():
#                     serialized_data = self.get_serializer(bookings, many=True).data
#                     logger.info(f"Today's bookings retrieved for user {user.email}")
#                     return Response(serialized_data, status=status.HTTP_200_OK)
#                 logger.warning(f"No bookings scheduled for today for user {user.email}")
#                 return response.Response(
#                     {"message": "No bookings scheduled for today."},
#                     status=status.HTTP_404_NOT_FOUND,
#                 )

#             if booking_id:
#                 booking = BookArtisan.objects.filter(artisan=user, id=booking_id).first()
#                 if booking:
#                     serialized_data = self.get_serializer(booking).data
#                     logger.info(f"Booking {booking_id} retrieved for user {user.email}")
#                     return Response(serialized_data, status=status.HTTP_200_OK)
#                 logger.warning(f"No booking found with ID {booking_id} for user {user.email}")
#                 return response.Response(
#                     {"message": "No booking found with the provided ID."},
#                     status=status.HTTP_404_NOT_FOUND,
#                 )

#             logger.warning(f"Invalid parameters for booking retrieval by user {user.email}")
#             return response.Response(
#                 {"message": "Please provide a valid schedule or booking_id parameter."},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )
#         except Exception as e:
#             logger.error(f"Error retrieving bookings for user {user.email}: {str(e)}")
#             return response.Response(
#                 {"error": f"An unexpected error occurred: {str(e)}"},
#                 status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             )

# @api_view(['POST'])
# def toggle_availability(request):
#     user = request.user
#     availability = request.data.get("isAvailable")

#     if availability is None:
#         logger.warning(f"Availability toggle failed for user {user.email}: Missing 'isAvailable' field")
#         return response.Response({"error": "The 'isAvailable' field is required."}, status=status.HTTP_400_BAD_REQUEST)

#     if not isinstance(availability, bool):
#         logger.warning(f"Availability toggle failed for user {user.email}: 'isAvailable' must be boolean")
#         return response.Response({"error": "The 'isAvailable' field must be a boolean (true or false)."}, status=status.HTTP_400_BAD_REQUEST)

#     with transaction.atomic():
#         artisan = get_object_or_404(User.objects.select_for_update(), id=user.id)
#         artisan.isAvailable = availability
#         artisan.save()
#         Notification.objects.create(
#             recipient=artisan,
#             message=f"Your availability has been set to {'available' if availability else 'unavailable'}.",
#             notification_type='AVAILABILITY'
#         )
#         logger.info(f"Availability toggled to {availability} for artisan {user.email}")
#     return response.Response({"message": "Availability toggled successfully!"}, status=status.HTTP_200_OK)

# @api_view(['GET'])
# def get_today_bookings(request):
#     user = request.user
#     date_today = datetime.today().date()
#     booking_id = request.query_params.get("booking_id")
#     schedule_filter = request.query_params.get("schedule", None)

#     try:
#         if schedule_filter is not None and schedule_filter.lower() == "today":
#             bookings = BookArtisan.objects.filter(
#                 Q(client=user) | Q(artisan=user), scheduled_date=date_today
#             )
#             if bookings.exists():
#                 serialized_data = ArtisanBookingSerializers(bookings, many=True).data
#                 logger.info(f"Today's bookings retrieved for user {user.email}")
#                 return Response(serialized_data, status=status.HTTP_200_OK)
#             logger.warning(f"No bookings scheduled for today for user {user.email}")
#             return Response(
#                 {"message": "No bookings scheduled for today."},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         if booking_id:
#             booking = BookArtisan.objects.filter(artisan=user, id=booking_id).first()
#             if booking:
#                 serialized_data = ArtisanBookingSerializers(booking).data
#                 logger.info(f"Booking {booking_id} retrieved for user {user.email}")
#                 return Response(serialized_data, status=status.HTTP_200_OK)
#             logger.warning(f"No booking found with ID {booking_id} for user {user.email}")
#             return Response(
#                 {"message": "No booking found with the provided ID."},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         logger.warning(f"Invalid parameters for booking retrieval by user {user.email}")
#         return Response(
#             {"message": "Please provide a valid schedule or booking_id parameter."},
#             status=status.HTTP_400_BAD_REQUEST,
#         )
#     except Exception as e:
#         logger.error(f"Error retrieving today's bookings for user {user.email}: {str(e)}")
#         return Response(
#             {"error": f"An unexpected error occurred: {str(e)}"},
#             status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#         )

# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# def pay_deposit(request, pk):
#     logger.info("pay_deposit HIT")

#     booking = get_object_or_404(BookArtisan, id=pk, client=request.user)

#     if booking.status != "Accepted":
#         return Response(
#             {"detail": "Booking must be accepted before paying deposit."},
#             status=status.HTTP_400_BAD_REQUEST
#         )

#     if not booking.agreed_amount or booking.agreed_amount <= 0:
#         return Response(
#             {"detail": "Agreed amount not set."},
#             status=status.HTTP_400_BAD_REQUEST
#         )

#     try:
#         # Use Decimal throughout for safe calculations
#         deposit_percentage = Decimal('0.70')

#         # Calculate deposit (do NOT save yet to avoid conflicting with model save())
#         deposit_amount = (booking.agreed_amount * deposit_percentage).quantize(Decimal('0.01'))
#         balance_amount = (booking.agreed_amount - deposit_amount).quantize(Decimal('0.01'))

#         reference = f"deposit_{booking.id}_{int(timezone.now().timestamp())}"

#         # Convert to kobo safely
#         amount_kobo = int(deposit_amount * Decimal('100'))

#         response = paystack.transaction.initialize(
#             reference=reference,
#             amount=amount_kobo,
#             email=request.user.email,
#             callback_url="http://127.0.0.1:8000/api/paystack/webhook/",  # Frontend success page
#             metadata={
#                 "booking_id": booking.id,
#                 "payment_type": "deposit",
#                 "user_id": str(request.user.id)
#             }
#         )

#         if response.get("status"):
#             # NOW save the calculated amounts (this will trigger model save() safely)
#             booking.deposit_amount = deposit_amount
#             booking.balance_amount = balance_amount
#             booking.save(update_fields=["deposit_amount", "balance_amount"])

#             return Response({
#                 "authorization_url": response["data"]["authorization_url"],
#                 "reference": response["data"]["reference"],
#                 "amount": deposit_amount  # e.g., Decimal('98000.00')
#             })

#         else:
#             return Response(
#                 {"detail": response.get("message", "Payment initialization failed")},
#                 status=status.HTTP_400_BAD_REQUEST
#             )

#     except Exception as e:
#         logger.exception("Deposit payment initialization failed")
#         return Response(
#             {"detail": f"Server error: {str(e)}"},
#             status=status.HTTP_500_INTERNAL_SERVER_ERROR
#         )
    
# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# def pay_balance(request, pk):
#     booking = get_object_or_404(
#         BookArtisan,
#         id=pk,
#         client=request.user
#     )

#     if booking.status != "Completed":
#         return Response(
#             {"detail": "Job must be marked as completed before paying balance."},
#             status=status.HTTP_400_BAD_REQUEST
#         )

#     if not booking.balance_amount or booking.balance_amount <= 0:
#         return Response(
#             {"detail": "No balance due."},
#             status=status.HTTP_400_BAD_REQUEST
#         )

#     reference = f"balance_{booking.id}_{int(timezone.now().timestamp())}"

#     try:
#         response = paystack.transaction.initialize(
#             reference=reference,
#             amount=int(booking.balance_amount * 100),
#             email=request.user.email,
#             callback_url="http://127.0.0.1:8000/api/paystack/webhook/",
#             metadata={
#                 "booking_id": booking.id,
#                 "payment_type": "balance",
#                 "user_id": str(request.user.id)
#             }
#         )

#         return Response({
#             "authorization_url": response["data"]["authorization_url"],
#             "reference": response["data"]["reference"],
#             "amount": booking.balance_amount
#         })

#     except Exception as e:
#         return Response(
#             {"detail": str(e)},
#             status=status.HTTP_500_INTERNAL_SERVER_ERROR
#         )

# @api_view(['POST', 'GET'])
# @permission_classes([AllowAny])
# def paystack_webhook(request):
#     """
#     Secure Paystack webhook handler
#     - Only POST webhooks with valid signature and 'charge.success' event will update the database
#     - GET (browser callback) will only return user-friendly status (no DB updates for security)
#     """

#     logger.info(f"Paystack callback/webhook hit: {request.method} from {request.META.get('REMOTE_ADDR')}")

#     # === HANDLE GET (browser redirect after payment) ===
#     if request.method == 'GET':
#         reference = request.query_params.get('reference') or request.query_params.get('trxref')
#         if not reference:
#             return Response({"message": "No reference provided"}, status=400)

#         # Verify transaction for user feedback
#         try:
#             verify_url = f"https://api.paystack.co/transaction/verify/{reference}"
#             headers = {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
#             resp = requests.get(verify_url, headers=headers)
#             data = resp.json()

#             if data.get("status") and data["data"]["status"] == "success":

#                 parts = reference.split("_")
#                 payment_type = parts[0]
#                 booking_id = int(parts[1])
#                 booking = BookArtisan.objects.get(id=booking_id)
#                 if payment_type == "deposit" and booking.status == "Accepted":
#                     booking.status = "Deposit Paid"
#                     booking.deposit_paid_at = timezone.now()
#                     booking.save()
#                 elif payment_type == "balance" and booking.status == "Completed":
#                     gross = to_decimal(booking.agreed_amount or booking.budget) # Use agreed_amount if set
#                     platform_fee_rate = Decimal('0.10')  # 10%
#                     vat_rate = Decimal('0.075')  # 7.5%

#                     platform_fee = (gross * platform_fee_rate).quantize(Decimal('0.01'))
#                     vat = (gross * vat_rate).quantize(Decimal('0.01'))  # VAT on gross
#                     net_amount = (gross - platform_fee).quantize(Decimal('0.01'))

#                     # Add net to artisan wallet
#                     booking.artisan.wallet_balance = (booking.artisan.wallet_balance + net_amount).quantize(Decimal('0.01'))
#                     booking.artisan.save(update_fields=['wallet_balance'])

#                     # Create transaction record (optional)
#                     Transaction.objects.create(
#                         artisan=booking.artisan,
#                         booking=booking,
#                         gross_amount=gross,
#                         platform_fee=platform_fee,
#                         vat=vat,
#                         net_amount=net_amount,
#                         transaction_type='EARNING'
#                     )

#                     booking.status = "Paid in Full"
#                     booking.fully_paid_at = timezone.now()
#                     booking.save()

#                 Notification.objects.create(
#                     recipient=booking.artisan,
#                     message=f"Payment received for booking {booking_id}.",
#                     notification_type="PAYMENT"
#                 )
#                 Notification.objects.create(
#                     recipient=booking.client,
#                     message=f"Payment successful for booking {booking_id}.",
#                     notification_type="PAYMENT"
#                 )

#                 return Response({
#                     "message": "Payment successful! Your booking has been updated.",
#                     "status": "success"
#                 }, status=200)
#             else:
#                 return Response({
#                     "message": "Payment failed or abandoned. Please try again.",
#                     "status": data["data"]["status"] if data.get("data") else "failed"
#                 }, status=200)
#         except Exception as e:
#             logger.exception("Error verifying GET callback")
#             return Response({"message": "Unable to verify payment status"}, status=500)

#     # === HANDLE POST (real webhook from Paystack) ===
#     # 1. Verify signature (critical for security)
#     signature = request.headers.get('x-paystack-signature')
#     if not signature:
#         logger.warning("Missing Paystack signature in webhook")
#         return Response(status=400)

#     computed_signature = hmac.new(
#         settings.PAYSTACK_SECRET_KEY.encode('utf-8'),
#         request.body,
#         hashlib.sha512
#     ).hexdigest()

#     if not hmac.compare_digest(computed_signature, signature):
#         logger.warning("Invalid Paystack webhook signature")
#         return Response(status=400)

#     # 2. Parse payload safely
#     try:
#         payload = json.loads(request.body)
#     except json.JSONDecodeError:
#         logger.error("Invalid JSON in webhook payload")
#         return Response(status=400)

#     event = payload.get('event')
#     if event != 'charge.success':
#         logger.info(f"Ignoring non-success event: {event}")
#         return Response({"message": "Event ignored"}, status=200)

#     data = payload.get('data', {})
#     reference = data.get('reference')
#     if not reference:
#         logger.warning("No reference in webhook payload")
#         return Response(status=400)

#     metadata = data.get('metadata', {})
#     amount_paid_kobo = data.get('amount', 0)  # Paystack sends amount in kobo

#     # Extract booking info
#     booking_id = metadata.get('booking_id')
#     payment_type = metadata.get('payment_type')

#     # Fallback parsing from reference (e.g., "deposit_4_1234567890")
#     if not booking_id or not payment_type:
#         try:
#             parts = reference.split("_")
#             if len(parts) >= 2:
#                 payment_type = parts[0]
#                 booking_id = int(parts[1])
#         except Exception:
#             logger.error(f"Failed to parse reference: {reference}")
#             return Response(status=400)

#     if not booking_id or not payment_type:
#         logger.error("Could not determine booking_id or payment_type")
#         return Response(status=400)

#     logger.info(f"Processing successful webhook for {payment_type} payment on booking {booking_id}, reference {reference}")

#     try:
#         with transaction.atomic():
#             booking = BookArtisan.objects.select_for_update().get(id=booking_id)

#             # Double-check amount (prevent tampering)
#             expected_field = booking.deposit_amount if payment_type == "deposit" else booking.balance_amount
#             if expected_field is None:
#                 logger.warning(f"{payment_type.capitalize()} amount not set on booking {booking_id}")
#                 return Response(status=200)

#             expected_kobo = int(expected_field * 100)
#             if amount_paid_kobo != expected_kobo:
#                 logger.warning(f"Amount mismatch: expected {expected_kobo}, got {amount_paid_kobo}")
#                 return Response(status=200)

#             # Update based on type and current status
#             updated = False
#             if payment_type == "deposit" and booking.status == "Accepted":
#                 booking.status = "Deposit Paid"
#                 booking.deposit_paid_at = timezone.now()
#                 booking.save(update_fields=["status", "deposit_paid_at"])
#                 updated = True

#                 Notification.objects.create(
#                     recipient=booking.artisan,
#                     message=f"Client has paid ₦{booking.deposit_amount:,} deposit. You can now start the job!",
#                     notification_type="PAYMENT"
#                 )
#                 Notification.objects.create(
#                     recipient=booking.client,
#                     message="Deposit payment successful! The artisan will begin work soon.",
#                     notification_type="PAYMENT"
#                 )

#             elif payment_type == "balance" and booking.status == "Completed":
#                 gross = to_decimal(booking.agreed_amount or booking.budget) # Use agreed_amount if set
#                 platform_fee_rate = Decimal('0.10')  # 10%
#                 vat_rate = Decimal('0.075')  # 7.5%

#                 platform_fee = (gross * platform_fee_rate).quantize(Decimal('0.01'))
#                 vat = (gross * vat_rate).quantize(Decimal('0.01'))  # VAT on gross
#                 net_amount = (gross - platform_fee).quantize(Decimal('0.01'))

#                 # Add net to artisan wallet
#                 booking.artisan.wallet_balance = (booking.artisan.wallet_balance + net_amount).quantize(Decimal('0.01'))
#                 booking.artisan.save(update_fields=['wallet_balance'])

#                 booking.status = "Paid in Full"
#                 booking.fully_paid_at = timezone.now()
#                 booking.save(update_fields=["status", "fully_paid_at"])
#                 updated = True

#                 # Create transaction record
#                 Transaction.objects.create(
#                     artisan=booking.artisan,
#                     booking=booking,
#                     gross_amount=gross,
#                     platform_fee=platform_fee,
#                     vat=vat,
#                     net_amount=net_amount,
#                     transaction_type='EARNING'
#                 )

#                 Notification.objects.create(
#                     recipient=booking.artisan,
#                     message=f"Final payment of ₦{booking.balance_amount:,} received. Funds released!",
#                     notification_type="PAYMENT"
#                 )
#                 Notification.objects.create(
#                     recipient=booking.client,
#                     message="Balance payment successful. Thank you!",
#                     notification_type="PAYMENT"
#                 )

#             if updated:
#                 logger.info(f"Successfully updated booking {booking_id} to {booking.status}")
#             else:
#                 logger.info(f"No update needed for booking {booking_id} (current status: {booking.status})")

#         return Response({"message": "Webhook processed successfully"}, status=200)

#     except BookArtisan.DoesNotExist:
#         logger.error(f"Booking {booking_id} not found")
#         return Response(status=200)
#     except Exception as e:
#         logger.exception(f"Error processing webhook for {reference}")
#         return Response(status=200)  # Always 200 to prevent retries
    
# class MaterialQuoteViewSet(viewsets.ModelViewSet):
#     queryset = MaterialQuote.objects.all()
#     serializer_class = MaterialQuoteSerializer
#     permission_classes = [IsAuthenticated]

#     def get_queryset(self):
#         user = self.request.user
#         if user.role == 'ARTISAN':
#             return MaterialQuote.objects.filter(artisan=user)
#         # Optionally allow admin/company to see all
#         elif user.is_staff or user.role == 'ADMIN': 
#             return MaterialQuote.objects.all()
#         return MaterialQuote.objects.none()

#     def perform_create(self, serializer):
#         quote = serializer.save(artisan=self.request.user)

#         # Notify client
#         Notification.objects.create(
#             recipient=quote.booking.client,
#             message=f"The artisan has requested ₦{quote.total_cost:,} for materials on your job: {quote.job_title}.",
#             notification_type='MATERIAL_REQUEST'
#         )

#     @action(detail=True, methods=['get'], url_path='my-requests')
#     def my_requests(self, request, booking_id=None):
#         """Extra endpoint: Get all material requests for a specific booking (useful for client)"""
#         quotes = MaterialQuote.objects.filter(booking_id=booking_id)
#         serializer = self.get_serializer(quotes, many=True)
#         return Response(serializer.data)

# def to_decimal(value) -> Decimal:
#     """
#     Convert int/float/str/Decimal to Decimal safely.
#     Using str() prevents float binary rounding issues.
#     """
#     if value is None:
#         return Decimal('0.00')
#     if isinstance(value, Decimal):
#         return value
#     try:
#         return Decimal(str(value))
#     except (InvalidOperation, ValueError, TypeError):
#         return Decimal('0.00')
    
# class ArtisanEarningsView(APIView):
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         if request.user.role != 'ARTISAN':
#             return Response({"detail": "Artisan only"}, status=403)

#         artisan = request.user

#         # Current balance
#         balance = artisan.wallet_balance

#         # Totals (net earnings from Paid in Full jobs)
#         now = timezone.now()
#         today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
#         week_start = today_start - timedelta(days=today_start.weekday())
#         month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

#         completed_jobs = BookArtisan.objects.filter(
#             artisan=artisan,
#             status="Paid in Full"
#         )

#         today_earnings = sum(
#             self.calculate_net(job.agreed_amount or job.budget)
#             for job in completed_jobs if job.fully_paid_at and job.fully_paid_at.date() == now.date()
#         )

#         week_earnings = sum(
#             self.calculate_net(job.agreed_amount or job.budget)
#             for job in completed_jobs if job.fully_paid_at and job.fully_paid_at >= week_start
#         )

#         month_earnings = sum(
#             self.calculate_net(job.agreed_amount or job.budget)
#             for job in completed_jobs if job.fully_paid_at and job.fully_paid_at >= month_start
#         )

#         return Response({
#             "balance": float(balance),
#             "total_earnings": {
#                 "today": float(today_earnings),
#                 "this_week": float(week_earnings),
#                 "last_month": float(month_earnings)
#             }
#         })

#     def calculate_net(self, gross):
#         gross = to_decimal(gross)

#         platform_fee = (gross * Decimal('0.10'))
#         vat = (gross * Decimal('0.075'))
#         return (gross - platform_fee - vat).quantize(Decimal('0.01'))

# class ArtisanPaymentsView(APIView):
#     permission_classes = [IsAuthenticated]

#     def calculate_net(self, gross):
#         gross = to_decimal(gross)

#         platform_fee = (gross * Decimal('0.10'))
#         vat = (gross * Decimal('0.075'))
#         net = gross - platform_fee - vat

#         return float(net.quantize(Decimal('0.01')))


#     def get(self, request):
#         print("=== ArtisanPaymentsView HIT ===", request.user.id, request.user.role)

#         try:
#             if request.user.role != 'ARTISAN':
#                 return Response({"detail": "Artisan only"}, status=403)

#             print("STEP 1: fetching recent")
#             recent = BookArtisan.objects.filter(
#                 artisan=request.user,
#                 status="Paid in Full"
#             ).select_related('client').prefetch_related('services').order_by('-fully_paid_at')[:10]
#             print("STEP 1b: recent count", recent.count())

#             recent_data = []
#             for idx, job in enumerate(recent, start=1):
#                 print("STEP 2: building recent item", idx, "job_id", job.id)

#                 client_photo = None
#                 if getattr(job.client, "photo", None):
#                     try:
#                         client_photo = job.client.photo.url
#                     except Exception as e:
#                         print("PHOTO URL ERROR:", str(e))
#                         client_photo = None

#                 amount = self.calculate_net(job.agreed_amount or job.budget)
#                 print("STEP 2b: amount computed", amount)

#                 recent_data.append({
#                     "client": {
#                         "id": str(job.client.id),
#                         "name": job.client.get_full_name() or job.client.email,
#                         "photo": client_photo
#                     },
#                     "service": ", ".join(s.name for s in job.services.all()),
#                     "location": job.address or "N/A",
#                     "date": job.fully_paid_at.strftime("%d/%m/%Y") if job.fully_paid_at else "N/A",
#                     "amount": amount,
#                     "status": "Paid"
#                 })

#             print("STEP 3: fetching pending")
#             pending = BookArtisan.objects.filter(
#                 artisan=request.user,
#                 status="Completed"
#             ).select_related('client').prefetch_related('services').order_by('-updated_at')
#             print("STEP 3b: pending count", pending.count())

#             pending_data = []
#             for idx, job in enumerate(pending, start=1):
#                 print("STEP 4: building pending item", idx, "job_id", job.id)

#                 client_photo = None
#                 if getattr(job.client, "photo", None):
#                     try:
#                         client_photo = job.client.photo.url
#                     except Exception as e:
#                         print("PHOTO URL ERROR:", str(e))
#                         client_photo = None

#                 pending_data.append({
#                     "client": {
#                         "id": str(job.client.id),
#                         "name": job.client.get_full_name() or job.client.email,
#                         "photo": client_photo
#                     },
#                     "service": ", ".join(s.name for s in job.services.all()),
#                     "location": job.address or "N/A",
#                     "date": job.scheduled_date.strftime("%d/%m/%Y") if job.scheduled_date else "N/A",
#                     "amount": float(job.balance_amount) if job.balance_amount else 0.00,
#                     "status": "Pending"
#                 })

#             return Response({
#                 "recent_payments": recent_data,
#                 "pending_payments": pending_data
#             })

#         except Exception as e:
#             # 👇 this WILL print the exact failing line
#             print("=== ArtisanPaymentsView CRASH ===", str(e))
#             print(traceback.format_exc())
#             raise 

# class ArtisanEarningsFilterView(APIView):
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         if request.user.role != 'ARTISAN':
#             return Response({"detail": "Artisan only"}, status=403)

#         artisan = request.user

#         # Current withdrawable balance
#         balance = float(artisan.wallet_balance)

#         # Get date range from query params (YYYY-MM-DD)
#         start_date_str = request.query_params.get('start_date')
#         end_date_str = request.query_params.get('end_date')

#         # Parse dates
#         start_date = parse_date(start_date_str) if start_date_str else None
#         end_date = parse_date(end_date_str) if end_date_str else None

#         # Base queryset: all Paid in Full jobs
#         completed_jobs = BookArtisan.objects.filter(
#             artisan=artisan,
#             status="Paid in Full"
#         )

#         # Apply date filter if both dates provided
#         if start_date and end_date:
#             # Include the end date fully
#             end_date_next = end_date + timedelta(days=1)
#             completed_jobs = completed_jobs.filter(
#                 fully_paid_at__gte=start_date,
#                 fully_paid_at__lt=end_date_next
#             )
#             logger.info(f"Filtered earnings for {artisan.email}: {start_date} to {end_date}")
#         elif start_date or end_date:
#             return Response({
#                 "detail": "Both start_date and end_date are required for filtering."
#             }, status=400)

#         # Calculate total net earnings
#         total_earnings = sum(
#             self.calculate_net(job.agreed_amount or job.budget)
#             for job in completed_jobs
#             if job.fully_paid_at 
#         )

#         return Response({
#             "balance": balance,
#             "total_earnings": float(total_earnings),
#             "start_date": start_date_str,
#             "end_date": end_date_str,
#         })

#     def calculate_net(self, gross):
#         if not gross:
#             return Decimal('0.00')
#         gross = Decimal(gross)
#         platform_fee = gross * Decimal('0.10')
#         vat = gross * Decimal('0.075')
#         net = gross - platform_fee - vat
#         return float(net.quantize(Decimal('0.01')))