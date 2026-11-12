from django.contrib import admin

from referrals.models import Referral, ReferralCode, ReferralReward


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "created_at")
    search_fields = ("code", "user__email")


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("referrer", "referred", "role", "status", "created_at", "qualified_at")
    list_filter = ("role", "status")
    search_fields = ("referrer__email", "referred__email")


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = ("owner", "kind", "percent", "status", "booking", "created_at", "used_at")
    list_filter = ("kind", "status")
    search_fields = ("owner__email",)
