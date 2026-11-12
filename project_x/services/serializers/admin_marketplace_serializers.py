from rest_framework import serializers

from main.models import Report
from main.serializers.report_serializers import person
from services.models.marketplace_models import JobRequest, Offer
from services.serializers.marketplace_serializers import AttachmentSerializer, live_offer_count, request_stage


def open_reports(**target):
    return Report.objects.filter(status="OPEN", **target).count()


class AdminOfferSerializer(serializers.ModelSerializer):
    """An offer as staff see it: every term, who sent it, and how it ended"""
    status = serializers.SerializerMethodField()
    provider = serializers.SerializerMethodField()
    revision_count = serializers.SerializerMethodField()
    open_reports = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = ("id", "request", "provider", "price", "description", "completion_days", "materials_included",
                  "labour_included", "additional_charges", "warranty", "assumptions", "is_estimate", "valid_until",
                  "status", "viewed_at", "rejection_note", "revision_count", "open_reports", "created_at",
                  "updated_at")
        read_only_fields = fields

    def get_status(self, offer):
        return "EXPIRED" if offer.is_expired else offer.status

    def get_provider(self, offer):
        return person(offer.provider)

    def get_revision_count(self, offer):
        return offer.revisions.count()

    def get_open_reports(self, offer):
        return open_reports(target_type="OFFER", offer=offer)


class AdminRequestSerializer(serializers.ModelSerializer):
    """A request in the staff list: what it is, whose it is, and where it stands"""
    reference = serializers.CharField(read_only=True)
    customer = serializers.SerializerMethodField()
    target_provider = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    services = serializers.SerializerMethodField()
    stage = serializers.SerializerMethodField()
    offer_count = serializers.SerializerMethodField()
    open_reports = serializers.SerializerMethodField()
    booking_id = serializers.IntegerField(source="booking.id", read_only=True, default=None)

    class Meta:
        model = JobRequest
        fields = ("id", "reference", "title", "category", "services", "customer", "fulfilment", "state", "city",
                  "budget_min", "budget_max", "budget_flexible", "deadline", "urgency", "visibility",
                  "target_provider", "status", "stage", "offer_count", "open_reports", "booking_id",
                  "published_at", "expires_at", "closed_at", "close_reason", "created_at", "updated_at")
        read_only_fields = fields

    def get_customer(self, job_request):
        return person(job_request.customer)

    def get_target_provider(self, job_request):
        return person(job_request.target_provider)

    def get_category(self, job_request):
        return job_request.category.name if job_request.category else None

    def get_services(self, job_request):
        return [service.name for service in job_request.services.all()]

    def get_stage(self, job_request):
        return request_stage(job_request)

    def get_offer_count(self, job_request):
        return live_offer_count(job_request)

    def get_open_reports(self, job_request):
        return open_reports(target_type="REQUEST", request=job_request)


class AdminRequestDetailSerializer(AdminRequestSerializer):
    """One request with everything staff may need to moderate it: the full text, files and every offer"""
    attachments = AttachmentSerializer(many=True, read_only=True)
    offers = AdminOfferSerializer(many=True, read_only=True)

    class Meta(AdminRequestSerializer.Meta):
        fields = AdminRequestSerializer.Meta.fields + ("description", "details", "address", "attachments", "offers")
        read_only_fields = fields


class StaffActionSerializer(serializers.Serializer):
    """Staff closing a request or withdrawing an offer say why; the people affected are told"""
    reason = serializers.CharField(min_length=5, max_length=1000, trim_whitespace=True)
