"""
Paying artisans. A customer's confirmation credits the artisan's wallet (job_process.release_booking);
from there the artisan asks to withdraw, staff approve, and Paystack sends the money to their bank.

    PENDING (money held from the wallet) -> PROCESSING (sent to Paystack) -> PAID
                                         -> REJECTED (by staff; refunded)    -> FAILED (refunded)
"""
import logging
import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import mixins, response, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from main.models import BankDetails, Notification, User
from main.paystack import PaystackError, bank_code_for, create_recipient, resolve_account, send_transfer
from main.permissions import IsArtisanPermission
from services.models.job_models import BookArtisan, Transaction, Withdrawal
from services.serializers.payout_serializers import (
    AdminWithdrawalSerializer, RejectWithdrawalSerializer, RequestWithdrawalSerializer, WithdrawalSerializer,
)
from services.views.job_process import artisan_share

logger = logging.getLogger('services')

# Paid by the customer and held by us, waiting for the job to be done and confirmed
ON_THE_WAY_STATUSES = ("Paid", "In Progress", "Completed")
OPEN_WITHDRAWAL_STATUSES = ("PENDING", "PROCESSING")
WITHDRAWAL_FILTERS = {
    "pending": Q(status="PENDING"),
    "processing": Q(status="PROCESSING"),
    "paid": Q(status="PAID"),
    "unsuccessful": Q(status__in=("FAILED", "REJECTED")),
}


def ensure_recipient(bank_details):
    """
    Makes sure the account can receive transfers. Bank details saved before withdrawals existed have no
    bank code or recipient yet, so they're checked with Paystack the first time the artisan withdraws.
    """
    if bank_details.recipient_code:
        return
    code = bank_details.bank_code or bank_code_for(bank_details.name)
    if not code:
        raise PaystackError("We don't recognise your bank. Please update your bank details.")
    account_name = resolve_account(bank_details.account_number, code)
    bank_details.bank_code = code
    bank_details.account_name = account_name
    bank_details.recipient_code = create_recipient(account_name, bank_details.account_number, code)
    bank_details.verified_at = timezone.now()
    bank_details.save(update_fields=["bank_code", "account_name", "recipient_code", "verified_at"])


def refund(withdrawal, new_status, note):
    """Puts a withdrawal's money back in the wallet. Call inside a transaction with the withdrawal locked."""
    artisan = User.objects.select_for_update().get(pk=withdrawal.artisan_id)
    artisan.wallet_balance = (artisan.wallet_balance + withdrawal.amount).quantize(Decimal('0.01'))
    artisan.save(update_fields=["wallet_balance"])
    withdrawal.status = new_status
    withdrawal.note = note
    withdrawal.completed_at = timezone.now()
    withdrawal.save(update_fields=["status", "note", "completed_at"])


def notify(withdrawal, message):
    Notification.objects.create(recipient=withdrawal.artisan, message=message, notification_type="PAYMENT")


def mark_paid(withdrawal):
    withdrawal.status = "PAID"
    withdrawal.completed_at = timezone.now()
    withdrawal.save(update_fields=["status", "completed_at"])
    notify(withdrawal, f"₦{withdrawal.amount:,} has been sent to your {withdrawal.bank_name} account.")


def mark_failed(withdrawal, reason):
    refund(withdrawal, "FAILED", reason)
    notify(withdrawal, f"We couldn't send ₦{withdrawal.amount:,} to your bank, so it's back in your balance. {reason}")


def record_transfer(event, data):
    """Applies a Paystack transfer webhook (transfer.success / .failed / .reversed). Safe to repeat."""
    reference = data.get("reference")
    with transaction.atomic():
        withdrawal = Withdrawal.objects.select_for_update().filter(reference=reference).first()
        if withdrawal is None:
            logger.warning(f"Transfer webhook for unknown reference {reference}")
            return None
        if event == "transfer.success" and withdrawal.status == "PROCESSING":
            mark_paid(withdrawal)
        elif event in ("transfer.failed", "transfer.reversed") and withdrawal.status in ("PROCESSING", "PAID"):
            reason = "The bank reversed the transfer." if event == "transfer.reversed" else "The bank didn't accept the transfer."
            mark_failed(withdrawal, reason)
        logger.info(f"{event} for withdrawal {withdrawal.id}: now {withdrawal.status}")
        return withdrawal


# ─────────────────────────────────────────────
# ARTISAN: WALLET
# ─────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsArtisanPermission])
def wallet(request):
    """The artisan's balance, money on the way, payout account and recent activity"""
    artisan = User.objects.get(pk=request.user.pk)
    on_the_way = sum(
        (artisan_share(booking.agreed_amount or booking.budget)
         for booking in BookArtisan.objects.filter(artisan=artisan, status__in=ON_THE_WAY_STATUSES, deposit_paid_at__isnull=True)),
        Decimal('0'),
    )
    earnings = Transaction.objects.filter(artisan=artisan, transaction_type='EARNING').select_related('booking__client')
    total_earned = earnings.aggregate(total=Sum('net_amount'))['total'] or Decimal('0')

    activity = [{
        "kind": "earning",
        "id": f"earning-{earning.id}",
        "amount": earning.net_amount,
        "description": (f"Job for {earning.booking.client.first_name} {earning.booking.client.last_name}".strip()
                        if earning.booking else "Job payment"),
        "status": "PAID",
        "date": earning.created_at,
    } for earning in earnings.order_by('-created_at')[:50]]
    activity += [{
        "kind": "withdrawal",
        "id": f"withdrawal-{withdrawal.id}",
        "amount": withdrawal.amount,
        "description": f"To {withdrawal.bank_name} ••{withdrawal.account_number[-4:]}",
        "status": withdrawal.status,
        "note": withdrawal.note,
        "date": withdrawal.created_at,
    } for withdrawal in Withdrawal.objects.filter(artisan=artisan)[:50]]
    activity.sort(key=lambda item: item["date"], reverse=True)

    bank = BankDetails.objects.filter(artisan=artisan).first()
    return response.Response({
        "available": artisan.wallet_balance,
        "on_the_way": on_the_way.quantize(Decimal('0.01')),
        "total_earned": total_earned,
        "has_open_withdrawal": Withdrawal.objects.filter(artisan=artisan, status__in=OPEN_WITHDRAWAL_STATUSES).exists(),
        "min_withdrawal": RequestWithdrawalSerializer().fields["amount"].min_value,
        "bank": bank and {
            "id": bank.id,
            "name": bank.name,
            "account_number": bank.account_number,
            "account_name": bank.account_name,
            "verified": bool(bank.recipient_code),
        },
        "activity": activity[:50],
    })


# ─────────────────────────────────────────────
# ARTISAN: WITHDRAWALS
# ─────────────────────────────────────────────
class WithdrawalViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """An artisan's own withdrawals; asking for one holds the amount until staff decide"""
    permission_classes = [IsArtisanPermission]
    serializer_class = WithdrawalSerializer

    def get_queryset(self):
        return Withdrawal.objects.filter(artisan=self.request.user)

    def create(self, request):
        serializer = RequestWithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        amount = serializer.validated_data["amount"]

        bank = BankDetails.objects.filter(artisan=request.user).first()
        if bank is None:
            return response.Response({"detail": "Add your bank details before withdrawing."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ensure_recipient(bank)
        except PaystackError as error:
            return response.Response(
                {"detail": f"We couldn't verify your bank account: {error}"}, status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            artisan = User.objects.select_for_update().get(pk=request.user.pk)
            if Withdrawal.objects.filter(artisan=artisan, status__in=OPEN_WITHDRAWAL_STATUSES).exists():
                return response.Response(
                    {"detail": "You already have a withdrawal in progress. Please wait for it to finish."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if amount > artisan.wallet_balance:
                return response.Response(
                    {"amount": [f"You can withdraw up to ₦{artisan.wallet_balance:,}."]}, status=status.HTTP_400_BAD_REQUEST
                )
            artisan.wallet_balance = (artisan.wallet_balance - amount).quantize(Decimal('0.01'))
            artisan.save(update_fields=["wallet_balance"])
            withdrawal = Withdrawal.objects.create(
                artisan=artisan,
                amount=amount,
                bank_name=bank.name,
                account_number=bank.account_number,
                account_name=bank.account_name,
                recipient_code=bank.recipient_code,
                reference=f"withdrawal_{uuid.uuid4().hex}",
            )
        logger.info(f"Withdrawal {withdrawal.id} of ₦{amount} requested by {artisan.email}")
        return response.Response(WithdrawalSerializer(withdrawal).data, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────
# STAFF: PAYOUTS
# ─────────────────────────────────────────────
class AdminWithdrawalViewSet(viewsets.ReadOnlyModelViewSet):
    """Staff review of withdrawals: list by status, approve (sends the transfer) or reject (refunds)"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminWithdrawalSerializer

    def get_queryset(self):
        queryset = Withdrawal.objects.select_related("artisan", "reviewed_by")
        status_filter = WITHDRAWAL_FILTERS.get(self.request.query_params.get("status", ""))
        return queryset.filter(status_filter) if status_filter is not None else queryset

    @action(detail=False, methods=["get"])
    def counts(self, request):
        return response.Response(Withdrawal.objects.aggregate(
            **{name: Count("pk", filter=condition) for name, condition in WITHDRAWAL_FILTERS.items()}
        ))

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        # Claim it first, in its own transaction, so two approvals can never send the money twice
        with transaction.atomic():
            withdrawal = get_withdrawal_for_update(pk)
            if withdrawal.status != "PENDING":
                return response.Response({"detail": "This withdrawal has already been handled."}, status=status.HTTP_400_BAD_REQUEST)
            withdrawal.status = "PROCESSING"
            withdrawal.reviewed_by = request.user
            withdrawal.reviewed_at = timezone.now()
            withdrawal.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        try:
            transfer = send_transfer(
                withdrawal.amount, withdrawal.recipient_code, withdrawal.reference, "Handwork Expert earnings"
            )
        except PaystackError as error:
            with transaction.atomic():
                withdrawal = get_withdrawal_for_update(pk)
                if withdrawal.status == "PROCESSING":
                    mark_failed(withdrawal, f"Paystack: {error}")
            logger.warning(f"Transfer for withdrawal {withdrawal.id} failed to start: {error}")
            return response.Response(self.get_serializer(withdrawal).data)

        with transaction.atomic():
            withdrawal = get_withdrawal_for_update(pk)
            withdrawal.transfer_code = transfer.get("transfer_code", "")
            withdrawal.save(update_fields=["transfer_code"])
            transfer_status = transfer.get("status")
            if transfer_status == "success" and withdrawal.status == "PROCESSING":
                mark_paid(withdrawal)
            elif transfer_status == "failed" and withdrawal.status == "PROCESSING":
                mark_failed(withdrawal, "The bank didn't accept the transfer.")
            elif transfer_status == "otp":
                # Stays PROCESSING: Paystack waits for an OTP, which API transfers can't give
                withdrawal.note = "Paystack is waiting for an OTP. Turn off OTP for transfers in the Paystack dashboard, then finish it there."
                withdrawal.save(update_fields=["note"])
        logger.info(f"Withdrawal {withdrawal.id} approved by {request.user.email}: transfer {transfer_status}")
        return response.Response(self.get_serializer(withdrawal).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = RejectWithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        with transaction.atomic():
            withdrawal = get_withdrawal_for_update(pk)
            if withdrawal.status != "PENDING":
                return response.Response({"detail": "This withdrawal has already been handled."}, status=status.HTTP_400_BAD_REQUEST)
            withdrawal.reviewed_by = request.user
            withdrawal.reviewed_at = timezone.now()
            withdrawal.save(update_fields=["reviewed_by", "reviewed_at"])
            refund(withdrawal, "REJECTED", reason)
            notify(withdrawal, f"Your withdrawal of ₦{withdrawal.amount:,} wasn't approved, so it's back in your balance: {reason}")
        return response.Response(self.get_serializer(withdrawal).data)


def get_withdrawal_for_update(pk):
    withdrawal = (Withdrawal.objects.select_for_update(of=("self",))
                  .select_related("artisan", "reviewed_by").filter(pk=pk).first())
    if withdrawal is None:
        raise Http404
    return withdrawal
