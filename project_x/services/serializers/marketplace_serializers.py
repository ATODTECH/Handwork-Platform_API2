from django.utils import timezone
from rest_framework import serializers

from main.models import Skill, User
from main.serializers.auth_serializers import review_summary
from services.models.job_models import BookArtisan
from services.models.marketplace_models import JobRequest, Offer, RequestAttachment
from services.models.services_models import Services

# Offers that still count: the customer can accept a submitted one, and an accepted one is the deal
LIVE_OFFER_STATUSES = ("SUBMITTED", "ACCEPTED")

# Once an offer is accepted, the request's stage follows its booking
BOOKING_STAGES = {
    "Accepted": "AWAITING_PAYMENT",
    "Paid": "IN_PROGRESS",
    "Deposit Paid": "IN_PROGRESS",
    "In Progress": "IN_PROGRESS",
    "Completed": "AWAITING_APPROVAL",
    "Released": "COMPLETED",
    "Paid in Full": "COMPLETED",
    "Cancelled": "CANCELLED",
    "Disputed": "DISPUTED",
    "Refunded": "REFUNDED",
}

MAX_DETAILS = 20
MAX_DETAIL_LENGTH = 500


def live_offer_count(job_request):
    count = getattr(job_request, "live_offers", None)
    if count is None:
        count = job_request.offers.filter(status__in=LIVE_OFFER_STATUSES).count()
    return count


def request_stage(job_request):
    """DRAFT, PUBLISHED, RECEIVING_OFFERS, EXPIRED, CLOSED, OFFER_ACCEPTED, then the booking's stage"""
    if job_request.status in ("DRAFT", "CLOSED"):
        return job_request.status
    if job_request.status == "OPEN":
        if job_request.is_expired:
            return "EXPIRED"
        return "RECEIVING_OFFERS" if live_offer_count(job_request) else "PUBLISHED"
    if job_request.booking is None:
        return "OFFER_ACCEPTED"
    return BOOKING_STAGES.get(job_request.booking.status, "IN_PROGRESS")


def full_name(user):
    return f"{user.first_name} {user.last_name}".strip()


def provider_summary(provider):
    """What a customer sees about a provider when comparing offers: no contact details or address"""
    reviews = review_summary(provider)
    skill = Skill.objects.filter(artisan=provider).prefetch_related("services").first()
    photos = [image.url for image in (skill.image1, skill.image2, skill.image3) if image] if skill else []
    return {
        "id": str(provider.id),
        "name": full_name(provider),
        "profile_image": provider.profile_image.url if provider.profile_image else None,
        "state": provider.state,
        "city": provider.city,
        "verified": provider.verification_status == "APPROVED",
        # Verified reviews only: each is tied to a job the customer paid for
        "average_rating": reviews["average"],
        "review_count": reviews["count"],
        "completed_jobs": BookArtisan.objects.filter(artisan=provider, status__in=("Released", "Paid in Full")).count(),
        "member_since": provider.date_joined.date().isoformat(),
        "services": [service.name for service in skill.services.all()] if skill else [],
        "work_photos": photos,
        "charges": {"lowest": str(skill.base_charge), "highest": str(skill.cap_charge)} if skill else None,
    }


class AttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = RequestAttachment
        fields = ("id", "kind", "url", "content_type", "size", "private", "created_at")
        read_only_fields = fields

    def get_url(self, attachment):
        # A signed link that expires after PRIVATE_MEDIA_URL_EXPIRE
        return attachment.file.url if attachment.file else None


class JobRequestSerializer(serializers.ModelSerializer):
    """The customer's own request: what they write, and everything about it they may see"""
    services = serializers.PrimaryKeyRelatedField(queryset=Services.objects.all(), many=True)
    reference = serializers.CharField(read_only=True)
    stage = serializers.SerializerMethodField()
    offer_count = serializers.SerializerMethodField()
    attachments = AttachmentSerializer(many=True, read_only=True)
    booking_id = serializers.IntegerField(source="booking.id", read_only=True, default=None)

    class Meta:
        model = JobRequest
        fields = ("id", "reference", "title", "category", "services", "description", "details", "fulfilment",
                  "state", "city", "address", "budget_min", "budget_max", "budget_flexible", "deadline", "urgency",
                  "visibility", "target_provider", "status", "stage", "offer_count", "attachments",
                  "published_at", "expires_at", "closed_at", "close_reason", "accepted_offer", "booking_id",
                  "created_at", "updated_at")
        read_only_fields = ("status", "published_at", "expires_at", "closed_at", "close_reason", "accepted_offer",
                            "created_at", "updated_at")

    def get_stage(self, job_request):
        return request_stage(job_request)

    def get_offer_count(self, job_request):
        return live_offer_count(job_request)

    def validate_title(self, title):
        title = title.strip()
        if len(title) < 5:
            raise serializers.ValidationError("Give your request a title of at least 5 characters.")
        return title

    def validate_description(self, description):
        description = description.strip()
        if len(description) < 20:
            raise serializers.ValidationError("Describe what you need in at least 20 characters.")
        if len(description) > 5000:
            raise serializers.ValidationError("Keep the description under 5,000 characters.")
        return description

    def validate_services(self, services):
        if not services:
            raise serializers.ValidationError("Choose at least one service.")
        kept = set(self.instance.services.values_list("id", flat=True)) if self.instance else set()
        hidden = [service.name for service in services if not service.active and service.id not in kept]
        if hidden:
            raise serializers.ValidationError(f"{', '.join(hidden)} isn't available.")
        return services

    def validate_details(self, details):
        if not isinstance(details, dict):
            raise serializers.ValidationError("Details must be a list of names and values.")
        if len(details) > MAX_DETAILS:
            raise serializers.ValidationError(f"Add at most {MAX_DETAILS} details.")
        for key, value in details.items():
            if not isinstance(value, (str, int, float, bool)) or value is None:
                raise serializers.ValidationError(f"{key} must be text or a number.")
            if len(str(key)) > 60 or len(str(value)) > MAX_DETAIL_LENGTH:
                raise serializers.ValidationError(f"{key} is too long.")
        return details

    def validate_deadline(self, deadline):
        changed = self.instance is None or self.instance.deadline != deadline
        if deadline and changed and deadline < timezone.localdate():
            raise serializers.ValidationError("Choose today or a later date.")
        return deadline

    def validate_target_provider(self, provider):
        if provider and (provider.role != "ARTISAN" or provider.verification_status != "APPROVED" or not provider.is_active):
            raise serializers.ValidationError("This provider isn't taking requests.")
        return provider

    def validate(self, attrs):
        instance = self.instance
        value = lambda field: attrs.get(field, getattr(instance, field, None) if instance else None)

        budget_min, budget_max = value("budget_min"), value("budget_max")
        for field, amount in (("budget_min", budget_min), ("budget_max", budget_max)):
            if amount is not None and amount < 0:
                raise serializers.ValidationError({field: "Enter an amount of zero or more."})
        if budget_min is not None and budget_max is not None and budget_max < budget_min:
            raise serializers.ValidationError({"budget_max": "The highest budget can't be below the lowest."})

        if instance and instance.status != "DRAFT":
            for field in ("visibility", "target_provider"):
                if field in attrs and attrs[field] != getattr(instance, field):
                    raise serializers.ValidationError({field: "This can't be changed once the request is published."})

        visibility, target = value("visibility") or "PUBLIC", value("target_provider")
        if visibility == "PRIVATE" and target is None:
            raise serializers.ValidationError({"target_provider": "Choose the provider to send this request to."})
        if visibility == "PUBLIC" and target is not None:
            raise serializers.ValidationError({"target_provider": "Only a private request goes to one provider."})
        return attrs


def publish_errors(job_request):
    """What's still missing before a request can be published"""
    errors = {}
    if not job_request.services.exists():
        errors["services"] = "Choose at least one service."
    if job_request.fulfilment in ("ON_SITE", "DELIVERY"):
        if not job_request.state:
            errors["state"] = "Choose the state."
        if not job_request.city:
            errors["city"] = "Enter the city or area."
    if job_request.fulfilment == "ON_SITE" and not job_request.address:
        errors["address"] = "Enter the address. Only the provider you choose will see it."
    if job_request.deadline and job_request.deadline < timezone.localdate():
        errors["deadline"] = "Choose today or a later date."
    return errors


class OfferSerializer(serializers.ModelSerializer):
    """A provider's offer. Providers write the terms; status and the rest move with the actions."""
    request = serializers.PrimaryKeyRelatedField(queryset=JobRequest.objects.all())
    status = serializers.SerializerMethodField()
    revision_count = serializers.SerializerMethodField()
    booking_id = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = ("id", "request", "price", "currency", "description", "completion_days", "materials_included",
                  "labour_included", "additional_charges", "warranty", "assumptions", "is_estimate", "valid_until",
                  "status", "viewed_at", "rejection_note", "revision_count", "booking_id", "created_at", "updated_at")
        read_only_fields = ("currency", "viewed_at", "rejection_note", "created_at", "updated_at")

    def get_status(self, offer):
        return "EXPIRED" if offer.is_expired else offer.status

    def get_revision_count(self, offer):
        return offer.revisions.count()

    def get_booking_id(self, offer):
        job_request = offer.request
        if offer.status == "ACCEPTED" and job_request.booking_id:
            return job_request.booking_id
        return None

    def get_fields(self):
        fields = super().get_fields()
        if isinstance(self.instance, Offer):
            # The request is fixed once the offer exists
            fields["request"].read_only = True
        return fields

    def validate_price(self, price):
        if price <= 0:
            raise serializers.ValidationError("Enter your price.")
        return price

    def validate_description(self, description):
        description = description.strip()
        if len(description) < 10:
            raise serializers.ValidationError("Explain what you'll do in at least 10 characters.")
        if len(description) > 3000:
            raise serializers.ValidationError("Keep it under 3,000 characters.")
        return description

    def validate_completion_days(self, days):
        if not 1 <= days <= 365:
            raise serializers.ValidationError("Enter between 1 and 365 days.")
        return days

    def validate_valid_until(self, valid_until):
        if valid_until and valid_until < timezone.localdate():
            raise serializers.ValidationError("Choose today or a later date.")
        return valid_until


class ProviderOfferSerializer(OfferSerializer):
    """The provider's own offer, with the request it's for"""
    request_summary = serializers.SerializerMethodField()

    class Meta(OfferSerializer.Meta):
        fields = OfferSerializer.Meta.fields + ("request_summary",)

    def get_request_summary(self, offer):
        job_request = offer.request
        return {
            "id": job_request.id,
            "reference": job_request.reference,
            "title": job_request.title,
            "stage": request_stage(job_request),
            "customer": job_request.customer.first_name,
            "state": job_request.state,
            "city": job_request.city,
            "deadline": job_request.deadline,
        }


class CustomerOfferSerializer(OfferSerializer):
    """An offer on the customer's request, with the provider and what changed"""
    provider = serializers.SerializerMethodField()
    revisions = serializers.SerializerMethodField()

    class Meta(OfferSerializer.Meta):
        fields = OfferSerializer.Meta.fields + ("provider", "revisions")
        read_only_fields = OfferSerializer.Meta.fields

    def get_provider(self, offer):
        return provider_summary(offer.provider)

    def get_revisions(self, offer):
        return [{"terms": revision.terms, "created_at": revision.created_at} for revision in offer.revisions.all()]


class ProviderRequestSerializer(serializers.ModelSerializer):
    """A request as a provider sees it: no address or private photos until their offer is accepted"""
    reference = serializers.CharField(read_only=True)
    category = serializers.SerializerMethodField()
    services = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    address = serializers.SerializerMethodField()
    stage = serializers.SerializerMethodField()
    offer_count = serializers.SerializerMethodField()
    my_offer = serializers.SerializerMethodField()
    saved = serializers.SerializerMethodField()
    private = serializers.SerializerMethodField()

    class Meta:
        model = JobRequest
        fields = ("id", "reference", "title", "category", "services", "description", "details", "fulfilment",
                  "state", "city", "address", "budget_min", "budget_max", "budget_flexible", "deadline", "urgency",
                  "private", "customer", "attachments", "stage", "offer_count", "my_offer", "saved",
                  "published_at", "expires_at")
        read_only_fields = fields

    def viewer(self):
        return self.context["request"].user

    def is_chosen(self, job_request):
        offer = job_request.accepted_offer
        return offer is not None and offer.provider_id == self.viewer().pk

    def get_category(self, job_request):
        category = job_request.category
        return {"id": category.id, "name": category.name} if category else None

    def get_services(self, job_request):
        return [{"id": service.id, "name": service.name} for service in job_request.services.all()]

    def get_customer(self, job_request):
        customer = job_request.customer
        return {"first_name": customer.first_name, "member_since": customer.date_joined.date().isoformat()}

    def get_attachments(self, job_request):
        attachments = job_request.attachments.all()
        if not self.is_chosen(job_request):
            attachments = [attachment for attachment in attachments if not attachment.private]
        return AttachmentSerializer(attachments, many=True).data

    def get_address(self, job_request):
        return job_request.address if self.is_chosen(job_request) else None

    def get_stage(self, job_request):
        stage = request_stage(job_request)
        # Other providers only need to know it's no longer taking offers
        if job_request.status == "ACCEPTED" and not self.is_chosen(job_request):
            return "OFFER_ACCEPTED"
        return stage

    def get_offer_count(self, job_request):
        return live_offer_count(job_request)

    def get_my_offer(self, job_request):
        offer = next((offer for offer in job_request.offers.all() if offer.provider_id == self.viewer().pk), None)
        return OfferSerializer(offer).data if offer else None

    def get_saved(self, job_request):
        saved = getattr(job_request, "is_saved", None)
        if saved is None:
            saved = self.viewer().saved_requests.filter(request=job_request).exists()
        return bool(saved)

    def get_private(self, job_request):
        return job_request.visibility == "PRIVATE"


class CloseRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True, required=False, allow_blank=True)


class RejectOfferSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True, required=False, allow_blank=True)


class AcceptOfferSerializer(serializers.Serializer):
    """The final agreement: the customer picks when the job happens and confirms the address"""
    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField(input_formats=["%H:%M", "%H:%M:%S"])
    address = serializers.CharField(max_length=400, trim_whitespace=True, required=False, allow_blank=True)

    def validate_scheduled_date(self, scheduled_date):
        if scheduled_date < timezone.localdate():
            raise serializers.ValidationError("Choose today or a later date.")
        return scheduled_date


class AttachmentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    private = serializers.BooleanField(required=False, default=False)
