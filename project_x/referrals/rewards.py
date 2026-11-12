"""The referral rules: linking a sign-up to a code, earning rewards on a first paid-out job, and using them."""
import logging
from decimal import Decimal

from django.utils import timezone

from main.models import Notification
from referrals.models import Referral, ReferralCode, ReferralReward

logger = logging.getLogger("services")

CENT = Decimal("0.01")


def link_referral(user, code):
    """Records who referred a new account. Only customer→customer and artisan→artisan count."""
    code = (code or "").strip().upper()
    if not code:
        return None
    owner = ReferralCode.objects.select_related("user").filter(code=code).first()
    if owner is None or owner.user_id == user.pk or not owner.user.is_active:
        return None
    if owner.user.role != user.role or Referral.objects.filter(referred=user).exists():
        return None
    referral = Referral.objects.create(referrer=owner.user, referred=user, role=user.role)
    logger.info(f"Referral: {user.email} signed up with {owner.user.email}'s code")
    return referral


def _qualify(referral, booking, kind, message):
    referral.status = "QUALIFIED"
    referral.qualifying_booking = booking
    referral.qualified_at = timezone.now()
    referral.save(update_fields=["status", "qualifying_booking", "qualified_at"])
    ReferralReward.objects.create(owner=referral.referrer, referral=referral, kind=kind)
    Notification.objects.create(recipient=referral.referrer, notification_type="PAYMENT", message=message)
    logger.info(f"Referral {referral.id} qualified on booking {booking.id}: {kind} for {referral.referrer.email}")


def reward_referrers(booking):
    """A paid-out job may be the customer's or the artisan's first: their referrer earns a reward. Call when releasing."""
    customer_referral = Referral.objects.select_related("referrer").filter(
        referred_id=booking.client_id, role="USER", status="SIGNED_UP").first()
    if customer_referral:
        _qualify(customer_referral, booking, "CUSTOMER_DISCOUNT",
                 f"{booking.client.first_name}, who you invited, finished their first job. You get 5% off your next booking.")
    if booking.artisan_id:
        artisan_referral = Referral.objects.select_related("referrer").filter(
            referred_id=booking.artisan_id, role="ARTISAN", status="SIGNED_UP").first()
        if artisan_referral:
            _qualify(artisan_referral, booking, "ARTISAN_FEE_CUT",
                     f"{booking.artisan.first_name}, who you invited, finished their first job. "
                     f"We'll take 5% less from your next job.")


def customer_discount_preview(booking):
    """What the customer would save on this booking: a reward already held for it, or their next available one"""
    held = ReferralReward.objects.filter(booking=booking, kind="CUSTOMER_DISCOUNT", status__in=("RESERVED", "USED")).first()
    reward = held or ReferralReward.objects.filter(owner_id=booking.client_id, kind="CUSTOMER_DISCOUNT", status="AVAILABLE").first()
    if reward is None or not booking.agreed_amount:
        return Decimal("0")
    return (booking.agreed_amount * reward.percent / 100).quantize(CENT)


def reserve_customer_discount(booking):
    """Holds the customer's next discount for this booking while they pay. Returns the discount. Call in a transaction."""
    reward = ReferralReward.objects.select_for_update().filter(booking=booking, kind="CUSTOMER_DISCOUNT", status="RESERVED").first()
    if reward is None:
        reward = (ReferralReward.objects.select_for_update()
                  .filter(owner_id=booking.client_id, kind="CUSTOMER_DISCOUNT", status="AVAILABLE").first())
        if reward is not None:
            reward.status = "RESERVED"
            reward.booking = booking
            reward.save(update_fields=["status", "booking"])
    discount = (booking.agreed_amount * reward.percent / 100).quantize(CENT) if reward else Decimal("0")
    if booking.referral_discount != discount:
        booking.referral_discount = discount
        booking.save(update_fields=["referral_discount"])
    return discount


def use_customer_discount(booking):
    """The payment went through: the held discount is spent"""
    ReferralReward.objects.filter(booking=booking, kind="CUSTOMER_DISCOUNT", status="RESERVED").update(
        status="USED", used_at=timezone.now())


def return_customer_discount(booking):
    """The booking was cancelled before paying, or refunded: the customer gets the discount back for another booking"""
    returned = ReferralReward.objects.filter(booking=booking, kind="CUSTOMER_DISCOUNT", status__in=("RESERVED", "USED")).update(
        status="AVAILABLE", booking=None, used_at=None)
    if booking.referral_discount and booking.status not in ("Refunded",):
        booking.referral_discount = Decimal("0")
        booking.save(update_fields=["referral_discount"])
    return returned


def artisan_fee_cut(booking):
    """Uses the artisan's next fee reward on this job, if they have one. Returns the percentage points taken off our fee."""
    reward = (ReferralReward.objects.select_for_update()
              .filter(owner_id=booking.artisan_id, kind="ARTISAN_FEE_CUT", status="AVAILABLE").first())
    if reward is None:
        return Decimal("0")
    reward.status = "USED"
    reward.booking = booking
    reward.used_at = timezone.now()
    reward.save(update_fields=["status", "booking", "used_at"])
    return reward.percent
