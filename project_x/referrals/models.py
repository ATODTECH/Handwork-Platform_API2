"""
Refer & earn. Each customer or artisan has a code. Someone who signs up with it is their referral; once the
referred person's first job is paid out to the artisan, the referrer earns a reward:
- a customer who referred a customer gets 5% off their next booking (we absorb it; the artisan is paid in full);
- an artisan who referred an artisan has 5% less taken from their next job (our fee drops from 10% to 5%).
Rewards are used automatically, one per booking.
"""
import secrets
from decimal import Decimal

from django.db import models

from main.models import User

REWARD_PERCENT = Decimal("5")
# Readable codes: no 0/O or 1/I to mix up when typed from a WhatsApp message
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 7


def new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not ReferralCode.objects.filter(code=code).exists():
            return code


class ReferralCode(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="referral_code")
    code = models.CharField(max_length=12, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} ({self.user.email})"

    @classmethod
    def for_user(cls, user):
        existing = cls.objects.filter(user=user).first()
        return existing or cls.objects.create(user=user, code=new_code())


class Referral(models.Model):
    STATUS = (
        ("SIGNED_UP", "Signed up"),
        # Their first job was paid out, so the referrer earned a reward
        ("QUALIFIED", "First job done"),
    )
    referrer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="referrals_made")
    referred = models.OneToOneField(User, on_delete=models.CASCADE, related_name="referred_by")
    # USER or ARTISAN: only same-role referrals are recorded
    role = models.CharField(max_length=7)
    status = models.CharField(max_length=10, choices=STATUS, default="SIGNED_UP")
    qualifying_booking = models.ForeignKey("services.BookArtisan", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    qualified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ReferralReward(models.Model):
    KIND = (
        ("CUSTOMER_DISCOUNT", "5% off your next booking"),
        ("ARTISAN_FEE_CUT", "5% less taken from your next job"),
    )
    STATUS = (
        ("AVAILABLE", "Ready to use"),
        # Held for a booking that's being paid for; goes back to AVAILABLE if that booking is cancelled
        ("RESERVED", "On a booking"),
        ("USED", "Used"),
    )
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="referral_rewards")
    referral = models.OneToOneField(Referral, on_delete=models.CASCADE, related_name="reward")
    kind = models.CharField(max_length=20, choices=KIND)
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=REWARD_PERCENT)
    status = models.CharField(max_length=10, choices=STATUS, default="AVAILABLE")
    booking = models.ForeignKey("services.BookArtisan", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
