"""
The request-and-offer marketplace.

A customer posts a JobRequest (what they need, with photos or videos, where, budget and deadline). Approved
providers who offer a matching service find it and send an Offer. The customer compares the offers and accepts
one, which creates a BookArtisan at "Accepted" with the offer's price. From there the booking follows the usual
flow: pay (we hold it) -> In Progress -> Completed -> Released, or Cancelled / Disputed.
"""
from datetime import timedelta

from django.db import models
from django.utils import timezone

from main.models import User
from main.storage import private_media_storage
from .services_models import Services, Category
from .job_models import BookArtisan

# How long a published request takes offers before it expires
REQUEST_OPEN_DAYS = 30


class JobRequest(models.Model):
    # Stored states. While a request is open its stage is worked out from its offers, and once an offer is
    # accepted from its booking (see request_stage in the serializers).
    STATUS = (
        ("DRAFT", "Draft"),
        ("OPEN", "Open"),          # published and taking offers
        ("ACCEPTED", "Accepted"),  # an offer was accepted; the booking carries it on
        ("CLOSED", "Closed"),      # the customer closed it without accepting
    )
    FULFILMENT = (
        ("ON_SITE", "At my location"),
        ("DELIVERY", "Made elsewhere and delivered"),
        ("REMOTE", "Remotely"),
    )
    URGENCY = (
        ("FLEXIBLE", "Flexible"),
        ("SOON", "Within a few days"),
        ("URGENT", "Urgent"),
    )
    VISIBILITY = (
        ("PUBLIC", "Any matching provider"),
        ("PRIVATE", "One provider I chose"),
    )

    customer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="job_requests",
                                 limit_choices_to={"role": "USER"})
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    services = models.ManyToManyField(Services, related_name="job_requests")
    title = models.CharField(max_length=120)
    description = models.TextField()
    # Category-specific details: quantity, materials, dimensions, size, vehicle make and year...
    details = models.JSONField(default=dict, blank=True)

    fulfilment = models.CharField(max_length=10, choices=FULFILMENT, default="ON_SITE")
    state = models.CharField(max_length=40, blank=True, default="")
    city = models.CharField(max_length=60, blank=True, default="")
    # Only the provider whose offer is accepted sees the address
    address = models.CharField(max_length=400, blank=True, default="")

    budget_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_flexible = models.BooleanField(default=False)
    # Preferred, not promised: nothing is agreed until the customer accepts an offer
    deadline = models.DateField(null=True, blank=True)
    urgency = models.CharField(max_length=10, choices=URGENCY, default="FLEXIBLE")

    visibility = models.CharField(max_length=10, choices=VISIBILITY, default="PUBLIC")
    # A private request goes to this provider only (the "Request a quote" button on their profile)
    target_provider = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="private_requests", limit_choices_to={"role": "ARTISAN"})

    status = models.CharField(max_length=10, choices=STATUS, default="DRAFT")
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.TextField(blank=True, default="")
    accepted_offer = models.OneToOneField("Offer", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    booking = models.OneToOneField(BookArtisan, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="job_request")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"REQ-{self.pk}: {self.title}"

    @property
    def reference(self):
        return f"REQ-{self.pk:06d}"

    @property
    def is_expired(self):
        return self.status == "OPEN" and self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def takes_offers(self):
        return self.status == "OPEN" and not self.is_expired

    def publish(self):
        now = timezone.now()
        self.status = "OPEN"
        self.published_at = self.published_at or now
        self.expires_at = now + timedelta(days=REQUEST_OPEN_DAYS)


def request_upload_path(attachment, filename):
    return f"request_media/{attachment.request_id}/{filename}"


class RequestAttachment(models.Model):
    """A photo or short video of what the customer needs. Kept private and served through signed links."""
    KIND = (("IMAGE", "Image"), ("VIDEO", "Video"))

    request = models.ForeignKey(JobRequest, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=5, choices=KIND)
    file = models.FileField(upload_to=request_upload_path, storage=private_media_storage)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    # Private ones are only shown to the provider whose offer is accepted
    private = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Offer(models.Model):
    # "Viewed" and "Expired" are worked out from viewed_at and valid_until; "Revised" from the revisions
    STATUS = (
        ("SUBMITTED", "Submitted"),
        ("ACCEPTED", "Accepted"),
        ("REJECTED", "Rejected"),
        ("WITHDRAWN", "Withdrawn"),
    )
    # The terms a provider can change when revising; each revision keeps a copy of the previous ones
    TERMS = ("price", "description", "completion_days", "materials_included", "labour_included",
             "additional_charges", "warranty", "assumptions", "is_estimate", "valid_until")

    request = models.ForeignKey(JobRequest, on_delete=models.CASCADE, related_name="offers")
    provider = models.ForeignKey(User, on_delete=models.PROTECT, related_name="offers",
                                 limit_choices_to={"role": "ARTISAN"})
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="NGN")
    description = models.TextField()
    completion_days = models.PositiveIntegerField()
    materials_included = models.BooleanField(default=False)
    labour_included = models.BooleanField(default=True)
    # Anything that may cost more, disclosed up front; blank means none
    additional_charges = models.TextField(blank=True, default="")
    warranty = models.CharField(max_length=300, blank=True, default="")
    assumptions = models.TextField(blank=True, default="")
    # A preliminary estimate whose final price depends on seeing the job (a car or an AC unit, say)
    is_estimate = models.BooleanField(default=False)
    valid_until = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=STATUS, default="SUBMITTED")
    viewed_at = models.DateTimeField(null=True, blank=True)
    rejection_note = models.CharField(max_length=1000, blank=True, default="")
    # Rejected only because the customer accepted another offer; reopened if that booking is cancelled
    closed_by_acceptance = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["request", "provider"], name="one_offer_per_provider_per_request"),
        ]

    def __str__(self):
        return f"Offer {self.pk} on REQ-{self.request_id}"

    @property
    def is_expired(self):
        return self.status == "SUBMITTED" and self.valid_until is not None and self.valid_until < timezone.localdate()

    def terms(self):
        values = {field: getattr(self, field) for field in self.TERMS}
        values["price"] = str(self.price)
        values["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        return values


class OfferRevision(models.Model):
    """The terms an offer had before a revision"""
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="revisions")
    terms = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class SavedRequest(models.Model):
    provider = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_requests")
    request = models.ForeignKey(JobRequest, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "request"], name="save_request_once"),
        ]
