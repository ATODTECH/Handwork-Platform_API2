"""
Requests and offers: customers post what they need, providers send offers, the customer accepts one.
See services/models/marketplace_models.py for the flow.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat.messaging import (
    broadcast_request_message, can_message_request, check_request_message, request_thread_problem,
)
from chat.models import Message
from chat.serializers import MessageSerializer
from django.core.exceptions import ValidationError as DjangoValidationError
from main.models import Notification, Skill, User
from services.models.job_models import BookArtisan
from services.models.marketplace_models import JobRequest, Offer, OfferRevision, RequestAttachment, SavedRequest
from services.serializers.marketplace_serializers import (
    LIVE_OFFER_STATUSES, AcceptOfferSerializer, AttachmentSerializer, AttachmentUploadSerializer,
    CloseRequestSerializer, CustomerOfferSerializer, JobRequestSerializer, OfferSerializer, ProviderOfferSerializer,
    ProviderRequestSerializer, RejectOfferSerializer, publish_errors,
)

logger = logging.getLogger("services")

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_IMAGES = 8
MAX_VIDEOS = 2
# How many matching providers hear about a new request, so they aren't flooded
NOTIFY_PROVIDERS_LIMIT = 30
# A request reopened after its booking is cancelled takes offers for at least this long
REOPEN_DAYS = 7


def notify(user, message):
    Notification.objects.create(recipient=user, message=message, notification_type="BOOKING")


def naira(amount):
    return f"₦{amount:,}"


def is_customer(user):
    return user.is_authenticated and user.role == "USER"


def is_active_provider(user):
    return (user.is_authenticated and user.role == "ARTISAN" and user.is_active
            and user.verification_status == "APPROVED")


def require_customer(user):
    if not is_customer(user):
        raise PermissionDenied("Only customers can do this.")


def require_provider(user):
    if not is_active_provider(user):
        raise PermissionDenied("Requests open up once our team has approved your account.")


def offered_service_ids(provider):
    return set(Skill.objects.filter(artisan=provider).values_list("services", flat=True)) - {None}


def matching_providers(job_request):
    """Approved providers who offer one of the request's services, near it when the job is on site"""
    if job_request.visibility == "PRIVATE":
        provider = job_request.target_provider
        return [provider] if provider and is_active_provider(provider) else []
    providers = User.objects.filter(
        role="ARTISAN", is_active=True, verification_status="APPROVED",
        skill__services__in=job_request.services.all(),
    ).distinct()
    if job_request.fulfilment == "ON_SITE" and job_request.state:
        providers = providers.filter(state__iexact=job_request.state)
    return list(providers.order_by("-last_login")[:NOTIFY_PROVIDERS_LIMIT])


def with_offer_counts(queryset):
    # distinct, so joins added by later filters (services) don't multiply the count
    return queryset.annotate(
        live_offers=Count("offers", filter=Q(offers__status__in=LIVE_OFFER_STATUSES), distinct=True)
    )


class JobRequestViewSet(viewsets.ModelViewSet):
    """
    Customers: their own requests (create, edit, publish, close, see offers, add photos and videos).
    Providers: GET /requests/feed to find work, GET /requests/{id} for one they can see, and save/unsave.
    """
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        user = self.request.user
        queryset = JobRequest.objects.select_related(
            "category", "booking", "accepted_offer", "customer", "target_provider"
        ).prefetch_related("services", "attachments")
        if is_customer(user):
            return with_offer_counts(queryset.filter(customer=user))
        if user.is_authenticated and user.role == "ARTISAN":
            # A subquery rather than a join on offers, which would limit the offer count to theirs
            visible = (Q(status="OPEN", visibility="PUBLIC") | Q(target_provider=user)
                       | Q(pk__in=Offer.objects.filter(provider=user).values("request")))
            queryset = queryset.filter(visible).exclude(status="DRAFT")
            return with_offer_counts(queryset).prefetch_related(
                Prefetch("offers", queryset=Offer.objects.filter(provider=user))
            ).annotate(is_saved=Exists(SavedRequest.objects.filter(provider=user, request=OuterRef("pk"))))
        return JobRequest.objects.none()

    def get_serializer_class(self):
        if self.request.user.is_authenticated and self.request.user.role == "ARTISAN":
            return ProviderRequestSerializer
        return JobRequestSerializer

    def get_object(self):
        job_request = super().get_object()
        user = self.request.user
        # Providers only look; approval is needed to see requests at all
        if user.role == "ARTISAN":
            require_provider(user)
        return job_request

    def list(self, request, *args, **kwargs):
        if request.user.role == "ARTISAN":
            return self.feed(request)
        queryset = self.get_queryset()
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())
        return Response(self.get_serializer(queryset, many=True).data)

    def create(self, request, *args, **kwargs):
        require_customer(request.user)
        serializer = JobRequestSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        job_request = serializer.save(customer=request.user)
        logger.info(f"Request {job_request.id} drafted by {request.user.email}")
        return Response(self.serialize(job_request), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        require_customer(request.user)
        job_request = self.get_object()
        if job_request.status not in ("DRAFT", "OPEN"):
            return Response({"detail": "This request can't be changed any more."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = JobRequestSerializer(job_request, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        job_request = serializer.save()
        if job_request.status == "OPEN":
            # Providers who already offered should know, in case they want to revise
            offers = job_request.offers.filter(status="SUBMITTED").select_related("provider")
            for offer in offers:
                notify(offer.provider, f"The customer updated their request \"{job_request.title}\". "
                                       f"Check it and revise your offer if you need to.")
        return Response(self.serialize(job_request))

    def destroy(self, request, *args, **kwargs):
        require_customer(request.user)
        job_request = self.get_object()
        if job_request.status != "DRAFT":
            return Response({"detail": "Only a draft can be deleted. Close the request instead."},
                            status=status.HTTP_400_BAD_REQUEST)
        for attachment in job_request.attachments.all():
            attachment.file.delete(save=False)
        job_request.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def serialize(self, job_request):
        job_request = self.get_queryset().get(pk=job_request.pk)
        return self.get_serializer(job_request).data

    # ── Customer actions ──────────────────────────

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        """Publish a draft, or renew a request that expired without an accepted offer"""
        require_customer(request.user)
        with transaction.atomic():
            job_request = get_object_or_404(JobRequest.objects.select_for_update(), pk=pk, customer=request.user)
            renewing = job_request.status == "OPEN" and job_request.is_expired
            if job_request.status != "DRAFT" and not renewing:
                return Response({"detail": "This request is already published."}, status=status.HTTP_400_BAD_REQUEST)
            errors = publish_errors(job_request)
            if errors:
                return Response(errors, status=status.HTTP_400_BAD_REQUEST)
            job_request.publish()
            job_request.save()
        if not renewing:
            providers = matching_providers(job_request)
            for provider in providers:
                if job_request.visibility == "PRIVATE":
                    message = (f"{request.user.first_name} asked you for a quote: \"{job_request.title}\". "
                               f"Send them an offer.")
                else:
                    message = f"New request near you: \"{job_request.title}\". Send an offer if you can do it."
                notify(provider, message)
            logger.info(f"Request {job_request.id} published; {len(providers)} providers told")
        return Response(self.serialize(job_request))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        require_customer(request.user)
        serializer = CloseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        with transaction.atomic():
            job_request = get_object_or_404(JobRequest.objects.select_for_update(), pk=pk, customer=request.user)
            if job_request.status != "OPEN":
                return Response({"detail": "Only a request that's taking offers can be closed."},
                                status=status.HTTP_400_BAD_REQUEST)
            job_request.status = "CLOSED"
            job_request.closed_at = timezone.now()
            job_request.close_reason = reason
            job_request.save()
            offers = list(job_request.offers.filter(status="SUBMITTED").select_related("provider"))
            for offer in offers:
                offer.status = "REJECTED"
                offer.rejection_note = "The customer closed this request."
                offer.save(update_fields=["status", "rejection_note", "updated_at"])
                notify(offer.provider, f"The customer closed their request \"{job_request.title}\", "
                                       f"so your offer is no longer needed.")
        return Response(self.serialize(job_request))

    @action(detail=True, methods=["get"])
    def offers(self, request, pk=None):
        """Every offer on the customer's request, for comparing. Opening them marks them viewed."""
        require_customer(request.user)
        job_request = get_object_or_404(JobRequest, pk=pk, customer=request.user)
        offers = (job_request.offers.exclude(status="WITHDRAWN")
                  .select_related("provider", "request").prefetch_related("revisions"))
        offers.filter(status="SUBMITTED", viewed_at__isnull=True).update(viewed_at=timezone.now())
        return Response(CustomerOfferSerializer(offers, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="attachments")
    def add_attachment(self, request, pk=None):
        """One photo or video per call, so the page can show each upload's progress"""
        require_customer(request.user)
        job_request = get_object_or_404(JobRequest, pk=pk, customer=request.user)
        if job_request.status not in ("DRAFT", "OPEN"):
            return Response({"detail": "Photos and videos can't be changed any more."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = AttachmentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        content_type = (upload.content_type or "").lower()
        existing = job_request.attachments.values_list("kind", flat=True)

        if content_type in IMAGE_TYPES:
            kind, limit, max_bytes, label = "IMAGE", MAX_IMAGES, MAX_IMAGE_BYTES, "photos"
        elif content_type in VIDEO_TYPES:
            kind, limit, max_bytes, label = "VIDEO", MAX_VIDEOS, MAX_VIDEO_BYTES, "videos"
        else:
            return Response({"file": ["Upload a JPG, PNG or WebP photo, or an MP4, MOV or WebM video."]},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > max_bytes:
            return Response({"file": [f"That file is too big. The limit is {max_bytes // (1024 * 1024)} MB."]},
                            status=status.HTTP_400_BAD_REQUEST)
        if list(existing).count(kind) >= limit:
            return Response({"file": [f"You can add up to {limit} {label}."]}, status=status.HTTP_400_BAD_REQUEST)
        if kind == "IMAGE":
            try:
                Image.open(upload).verify()
            except (UnidentifiedImageError, OSError, SyntaxError):
                return Response({"file": ["That photo couldn't be read. Try another one."]},
                                status=status.HTTP_400_BAD_REQUEST)
            upload.seek(0)

        attachment = RequestAttachment.objects.create(
            request=job_request, kind=kind, file=upload, content_type=content_type, size=upload.size,
            private=serializer.validated_data["private"],
        )
        return Response(AttachmentSerializer(attachment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"attachments/(?P<attachment_id>\d+)")
    def remove_attachment(self, request, pk=None, attachment_id=None):
        require_customer(request.user)
        job_request = get_object_or_404(JobRequest, pk=pk, customer=request.user)
        if job_request.status not in ("DRAFT", "OPEN"):
            return Response({"detail": "Photos and videos can't be changed any more."}, status=status.HTTP_400_BAD_REQUEST)
        attachment = get_object_or_404(RequestAttachment, pk=attachment_id, request=job_request)
        attachment.file.delete(save=False)
        attachment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Conversations before an offer is accepted ──────────

    def thread(self, request, pk):
        """
        The request and the provider of the conversation asked for. A provider's is always their own; the
        customer names the provider (?provider= or "provider" in the body).
        """
        job_request = get_object_or_404(JobRequest.objects.select_related("customer"), pk=pk)
        user = request.user
        if user.pk == job_request.customer_id:
            provider_id = request.query_params.get("provider") or request.data.get("provider")
            try:
                provider = User.objects.filter(pk=provider_id, role="ARTISAN").first() if provider_id else None
            except (DjangoValidationError, ValueError):
                provider = None
        elif user.role == "ARTISAN":
            provider = user
        else:
            raise NotFound()
        problem = request_thread_problem(job_request, user, provider)
        if problem == "Not authorized":
            raise NotFound()
        if problem:
            raise PermissionDenied(problem) if user.role == "ARTISAN" else ValidationError({"provider": [problem]})
        return job_request, provider

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        """GET: the conversation and whether it's open. POST {text}: send a message."""
        job_request, provider = self.thread(request, pk)
        if request.method == "POST":
            text = str(request.data.get("text") or "").strip()
            problem = check_request_message(job_request, request.user, provider, text)
            if problem:
                return Response({"detail": problem}, status=status.HTTP_400_BAD_REQUEST)
            message = Message.objects.create(request=job_request, provider=provider, sender=request.user, text=text)
            broadcast_request_message(message)
            return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)

        messages = (Message.objects.filter(request=job_request, provider=provider)
                    .select_related("sender").order_by("timestamp", "id"))
        return Response({
            "messages": MessageSerializer(messages, many=True).data,
            "can_send": can_message_request(job_request),
        })

    @action(detail=True, methods=["post"], url_path="messages/read")
    def read_messages(self, request, pk=None):
        """Mark the other person's messages in this conversation as read"""
        job_request, provider = self.thread(request, pk)
        updated = (Message.objects.filter(request=job_request, provider=provider, is_read=False)
                   .exclude(sender=request.user).update(is_read=True))
        return Response({"updated": updated})

    # ── Provider actions ──────────────────────────

    @action(detail=False, methods=["get"])
    def feed(self, request):
        """
        Open requests a provider can offer on: those for a service they offer (or every one with ?all=true),
        plus any sent to them privately. Filters: category, service, state, budget (their lowest acceptable),
        deadline_before, search, saved=true.
        """
        user = request.user
        require_provider(user)
        params = request.query_params
        now = timezone.now()
        queryset = self.get_queryset().filter(status="OPEN", expires_at__gt=now)

        if params.get("saved") == "true":
            queryset = queryset.filter(is_saved=True)
        elif params.get("all") != "true":
            queryset = queryset.filter(Q(services__in=offered_service_ids(user)) | Q(target_provider=user))
        # Someone else's private request never shows
        queryset = queryset.filter(Q(visibility="PUBLIC") | Q(target_provider=user))

        if params.get("category"):
            queryset = queryset.filter(category_id=params["category"])
        if params.get("service"):
            queryset = queryset.filter(services__id=params["service"])
        if params.get("state"):
            queryset = queryset.filter(state__iexact=params["state"])
        if params.get("budget"):
            # Requests whose budget reaches this amount, or that have no ceiling
            queryset = queryset.filter(
                Q(budget_max__gte=params["budget"]) | Q(budget_max__isnull=True) | Q(budget_flexible=True)
            )
        if params.get("deadline_before"):
            queryset = queryset.filter(deadline__lte=params["deadline_before"])
        if params.get("search"):
            term = params["search"].strip()
            queryset = queryset.filter(Q(title__icontains=term) | Q(description__icontains=term))

        queryset = queryset.distinct().order_by("-published_at")
        return Response(ProviderRequestSerializer(queryset, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post", "delete"], url_path="save")
    def save_request(self, request, pk=None):
        require_provider(request.user)
        job_request = self.get_object()
        if request.method == "POST":
            SavedRequest.objects.get_or_create(provider=request.user, request=job_request)
        else:
            SavedRequest.objects.filter(provider=request.user, request=job_request).delete()
        return Response({"saved": request.method == "POST"})


class OfferViewSet(viewsets.ModelViewSet):
    """
    Providers: their own offers (send, revise, withdraw).
    Customers: accept or reject offers on their requests (list them with GET /requests/{id}/offers).
    """
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        user = self.request.user
        queryset = Offer.objects.select_related("request", "request__customer", "request__booking", "provider")
        if user.role == "ARTISAN":
            queryset = queryset.filter(provider=user)
            if self.request.query_params.get("status"):
                queryset = queryset.filter(status=self.request.query_params["status"].upper())
            return queryset.order_by("-updated_at")
        if is_customer(user):
            return queryset.filter(request__customer=user).exclude(status="WITHDRAWN")
        return Offer.objects.none()

    def get_serializer_class(self):
        if self.request.user.role == "ARTISAN":
            return ProviderOfferSerializer
        return CustomerOfferSerializer

    def create(self, request, *args, **kwargs):
        provider = request.user
        require_provider(provider)
        serializer = OfferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        terms = {field: value for field, value in serializer.validated_data.items() if field != "request"}

        with transaction.atomic():
            job_request = get_object_or_404(
                JobRequest.objects.select_for_update(), pk=serializer.validated_data["request"].pk
            )
            if job_request.visibility == "PRIVATE" and job_request.target_provider_id != provider.pk:
                return Response({"request": ["This request isn't open to you."]}, status=status.HTTP_400_BAD_REQUEST)
            if not job_request.takes_offers:
                return Response({"request": ["This request isn't taking offers any more."]},
                                status=status.HTTP_400_BAD_REQUEST)
            if job_request.visibility == "PUBLIC":
                if not set(job_request.services.values_list("id", flat=True)) & offered_service_ids(provider):
                    return Response({"request": ["This request is for a service you don't offer. "
                                                 "Add it to your services first."]},
                                    status=status.HTTP_400_BAD_REQUEST)

            offer = Offer.objects.filter(request=job_request, provider=provider).first()
            if offer and offer.status in ("SUBMITTED", "ACCEPTED"):
                return Response({"detail": "You've already sent an offer. Revise it instead."},
                                status=status.HTTP_400_BAD_REQUEST)
            if offer:
                # A new offer after withdrawing or being turned down: keep the old terms in the history
                OfferRevision.objects.create(offer=offer, terms=offer.terms())
                for field, value in terms.items():
                    setattr(offer, field, value)
                offer.status = "SUBMITTED"
                offer.viewed_at = None
                offer.rejection_note = ""
                offer.closed_by_acceptance = False
                offer.save()
            else:
                offer = Offer.objects.create(request=job_request, provider=provider, **terms)

        notify(job_request.customer, f"{provider.first_name} sent you an offer of {naira(offer.price)} for "
                                     f"\"{job_request.title}\". Compare your offers and pick the best one.")
        logger.info(f"Offer {offer.id} on request {job_request.id} from {provider.email}")
        return Response(ProviderOfferSerializer(offer, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        """Revise an offer. The customer sees the change and the earlier terms."""
        require_provider(request.user)
        serializer = OfferSerializer(data=request.data, partial=True, instance=Offer())
        serializer.is_valid(raise_exception=True)
        changes = {field: value for field, value in serializer.validated_data.items() if field in Offer.TERMS}

        with transaction.atomic():
            offer = get_object_or_404(
                Offer.objects.select_for_update().select_related("request"), pk=kwargs["pk"], provider=request.user
            )
            if offer.status != "SUBMITTED":
                return Response({"detail": "Only an offer the customer hasn't answered can be revised."},
                                status=status.HTTP_400_BAD_REQUEST)
            if not offer.request.takes_offers:
                return Response({"detail": "This request isn't taking offers any more."},
                                status=status.HTTP_400_BAD_REQUEST)
            changed = {field: value for field, value in changes.items() if getattr(offer, field) != value}
            if changed:
                OfferRevision.objects.create(offer=offer, terms=offer.terms())
                for field, value in changed.items():
                    setattr(offer, field, value)
                offer.viewed_at = None
                offer.save()

        if changed:
            notify(offer.request.customer, f"{request.user.first_name} revised their offer for "
                                           f"\"{offer.request.title}\" (now {naira(offer.price)}).")
        return Response(ProviderOfferSerializer(offer, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        require_provider(request.user)
        with transaction.atomic():
            offer = get_object_or_404(
                Offer.objects.select_for_update().select_related("request"), pk=pk, provider=request.user
            )
            if offer.status != "SUBMITTED":
                return Response({"detail": "Only an offer the customer hasn't answered can be withdrawn."},
                                status=status.HTTP_400_BAD_REQUEST)
            offer.status = "WITHDRAWN"
            offer.save(update_fields=["status", "updated_at"])
        notify(offer.request.customer, f"{request.user.first_name} withdrew their offer for \"{offer.request.title}\".")
        return Response(ProviderOfferSerializer(offer, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        require_customer(request.user)
        serializer = RejectOfferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        with transaction.atomic():
            offer = get_object_or_404(
                Offer.objects.select_for_update().select_related("request", "provider"),
                pk=pk, request__customer=request.user,
            )
            if offer.status != "SUBMITTED":
                return Response({"detail": "Only an offer you haven't answered can be turned down."},
                                status=status.HTTP_400_BAD_REQUEST)
            offer.status = "REJECTED"
            offer.rejection_note = reason
            offer.save(update_fields=["status", "rejection_note", "updated_at"])
        notify(offer.provider, f"{request.user.first_name} turned down your offer for \"{offer.request.title}\"."
                               f"{f' Reason: {reason}' if reason else ''}")
        return Response(CustomerOfferSerializer(offer, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """
        Accept an offer: the booking is created at "Accepted" with the offer's price, the other offers are
        turned down, and the customer pays it as usual (POST /api/book/{id}/pay).
        """
        require_customer(request.user)
        serializer = AcceptOfferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreement = serializer.validated_data

        with transaction.atomic():
            job_request = get_object_or_404(
                JobRequest.objects.select_for_update(of=("self",)), offers__pk=pk, customer=request.user
            )
            offer = get_object_or_404(
                Offer.objects.select_for_update(of=("self",)).select_related("provider"), pk=pk, request=job_request
            )
            if not job_request.takes_offers:
                return Response({"detail": "This request isn't taking offers any more."},
                                status=status.HTTP_400_BAD_REQUEST)
            if offer.status != "SUBMITTED":
                return Response({"detail": "This offer can't be accepted. Ask the provider for a new one."},
                                status=status.HTTP_400_BAD_REQUEST)
            if offer.is_expired:
                return Response({"detail": "This offer has expired. Ask the provider to renew it."},
                                status=status.HTTP_400_BAD_REQUEST)
            if not is_active_provider(offer.provider):
                return Response({"detail": "This provider isn't taking work right now."},
                                status=status.HTTP_400_BAD_REQUEST)
            address = agreement.get("address") or job_request.address
            if job_request.fulfilment in ("ON_SITE", "DELIVERY") and not address:
                return Response({"address": ["Enter the address for the job."]}, status=status.HTTP_400_BAD_REQUEST)

            booking = BookArtisan.objects.create(
                client=request.user,
                artisan=offer.provider,
                description=f"{job_request.title}\n\n{job_request.description}",
                budget=job_request.budget_max or job_request.budget_min or offer.price,
                agreed_amount=offer.price,
                address=address,
                scheduled_date=agreement["scheduled_date"],
                scheduled_time=agreement["scheduled_time"],
                status="Accepted",
            )
            requested = set(job_request.services.values_list("id", flat=True))
            booking.services.set((requested & offered_service_ids(offer.provider)) or requested)

            offer.status = "ACCEPTED"
            offer.save(update_fields=["status", "updated_at"])
            others = list(job_request.offers.filter(status="SUBMITTED").exclude(pk=offer.pk).select_related("provider"))
            for other in others:
                other.status = "REJECTED"
                other.rejection_note = "The customer chose another offer."
                other.closed_by_acceptance = True
                other.save(update_fields=["status", "rejection_note", "closed_by_acceptance", "updated_at"])

            job_request.status = "ACCEPTED"
            job_request.accepted_offer = offer
            job_request.booking = booking
            job_request.save()
            # The booking's chat opens with what they said about the request
            Message.objects.filter(request=job_request, provider=offer.provider, booking__isnull=True).update(booking=booking)

        notify(offer.provider, f"{request.user.first_name} accepted your offer of {naira(offer.price)} for "
                               f"\"{job_request.title}\". You can start once they've paid.")
        notify(request.user, f"You accepted {offer.provider.first_name}'s offer. Pay {naira(offer.price)} to "
                             f"confirm the booking. We hold the money until you confirm the job is done.")
        for other in others:
            notify(other.provider, f"The customer chose another offer for \"{job_request.title}\".")
        logger.info(f"Offer {offer.id} accepted; booking {booking.id} created for request {job_request.id}")
        return Response({
            "offer": CustomerOfferSerializer(offer, context={"request": request}).data,
            "booking_id": booking.id,
        }, status=status.HTTP_201_CREATED)


def reopen_request_after_cancel(booking):
    """
    A customer who declines the price after accepting an offer cancels the booking. Its request starts taking
    offers again, and the offers turned down only because of that acceptance come back.
    Call inside the transaction that cancels the booking.
    """
    job_request = JobRequest.objects.select_for_update().filter(booking=booking).first()
    if job_request is None:
        return
    accepted = job_request.accepted_offer
    if accepted is not None:
        accepted.status = "REJECTED"
        accepted.rejection_note = "The customer cancelled after accepting."
        accepted.save(update_fields=["status", "rejection_note", "updated_at"])
    job_request.status = "OPEN"
    job_request.booking = None
    job_request.accepted_offer = None
    job_request.expires_at = max(job_request.expires_at or timezone.now(), timezone.now() + timedelta(days=REOPEN_DAYS))
    job_request.save()
    restored = job_request.offers.filter(status="REJECTED", closed_by_acceptance=True).select_related("provider")
    for offer in restored:
        offer.status = "SUBMITTED"
        offer.rejection_note = ""
        offer.closed_by_acceptance = False
        offer.save(update_fields=["status", "rejection_note", "closed_by_acceptance", "updated_at"])
        notify(offer.provider, f"\"{job_request.title}\" is taking offers again, and yours is back in the running.")
