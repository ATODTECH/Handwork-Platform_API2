from decimal import Decimal

from rest_framework import serializers

from services.models.job_models import Withdrawal

MIN_WITHDRAWAL = Decimal('1000')


class WithdrawalSerializer(serializers.ModelSerializer):
    """A withdrawal, as the artisan sees it"""

    class Meta:
        model = Withdrawal
        fields = ("id", "amount", "status", "bank_name", "account_number", "account_name", "note",
                  "created_at", "completed_at")
        read_only_fields = fields


class RequestWithdrawalSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=MIN_WITHDRAWAL)


class AdminWithdrawalSerializer(serializers.ModelSerializer):
    """Everything staff need to approve a withdrawal"""
    artisan = serializers.SerializerMethodField()
    reviewed_by = serializers.SerializerMethodField()

    class Meta:
        model = Withdrawal
        fields = ("id", "artisan", "amount", "status", "bank_name", "account_number", "account_name", "note",
                  "reference", "transfer_code", "reviewed_by", "reviewed_at", "completed_at", "created_at")
        read_only_fields = fields

    def get_artisan(self, withdrawal):
        artisan = withdrawal.artisan
        return {
            "id": str(artisan.id),
            "name": f"{artisan.first_name} {artisan.last_name}".strip(),
            "email": artisan.email,
            "wallet_balance": artisan.wallet_balance,
        }

    def get_reviewed_by(self, withdrawal):
        reviewer = withdrawal.reviewed_by
        return f"{reviewer.first_name} {reviewer.last_name}".strip() or reviewer.email if reviewer else None


class RejectWithdrawalSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True)
