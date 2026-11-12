from rest_framework import response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from referrals.models import REWARD_PERCENT, Referral, ReferralCode, ReferralReward


def short_name(user):
    """"Chi T.": enough to recognise a friend, without their full name"""
    last = (user.last_name or "").strip()
    return f"{user.first_name} {last[:1]}.".strip() if last else (user.first_name or "A friend")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_referrals(request):
    """The signed-in customer's or artisan's code, who signed up with it, and their rewards"""
    user = request.user
    code = ReferralCode.for_user(user)
    referrals = Referral.objects.filter(referrer=user).select_related("referred")
    rewards = ReferralReward.objects.filter(owner=user).select_related("referral__referred", "booking")
    return response.Response({
        "code": code.code,
        "role": user.role,
        "percent": str(REWARD_PERCENT),
        "referrals": [{
            "id": referral.id,
            "name": short_name(referral.referred),
            "status": referral.status,
            "joined_at": referral.created_at,
            "qualified_at": referral.qualified_at,
        } for referral in referrals],
        "rewards": [{
            "id": reward.id,
            "kind": reward.kind,
            "percent": str(reward.percent),
            "status": reward.status,
            "from": short_name(reward.referral.referred),
            "booking_id": reward.booking_id,
            "earned_at": reward.created_at,
            "used_at": reward.used_at,
        } for reward in rewards],
        "available": rewards.filter(status="AVAILABLE").count(),
    })
