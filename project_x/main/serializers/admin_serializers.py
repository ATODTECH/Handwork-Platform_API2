from rest_framework import serializers

from main.models import BankDetails, Skill, User
from main.serializers.auth_serializers import SkillSerializer


class AdminBankDetailsSerializer(serializers.ModelSerializer):
    verified = serializers.SerializerMethodField()

    class Meta:
        model = BankDetails
        fields = ("name", "account_number", "account_name", "verified")

    def get_verified(self, bank_details):
        """Checked with Paystack and ready for transfers"""
        return bool(bank_details.recipient_code)


class AdminArtisanSerializer(serializers.ModelSerializer):
    """Everything staff need to review an artisan: identity, work profile and payout account."""

    verified_by = serializers.SerializerMethodField()
    skill = serializers.SerializerMethodField()
    bank_details = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id", "first_name", "last_name", "email", "contact", "profile_image",
            "address", "city", "state", "nin", "document", "document_type",
            "onboardingComplete", "verification_status", "verification_note",
            "verified_at", "verified_by", "date_joined", "skill", "bank_details",
        )
        read_only_fields = fields

    def get_verified_by(self, artisan):
        reviewer = artisan.verified_by
        return f"{reviewer.first_name} {reviewer.last_name}".strip() or reviewer.email if reviewer else None

    def get_skill(self, artisan):
        # Prefetched by the view; an artisan has at most one skill profile in practice
        skill = next(iter(artisan.skill_set.all()), None)
        return SkillSerializer(skill).data if skill else None

    def get_bank_details(self, artisan):
        try:
            return AdminBankDetailsSerializer(artisan.bankdetails).data
        except BankDetails.DoesNotExist:
            return None


class RejectArtisanSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True)
