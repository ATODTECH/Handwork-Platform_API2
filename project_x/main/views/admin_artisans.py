import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from rest_framework import response, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser

from main.models import Notification, Skill, User
from main.serializers.admin_serializers import AdminArtisanSerializer, RejectArtisanSerializer

logger = logging.getLogger('main')

# `?status=` values: "incomplete" is an artisan who hasn't finished onboarding yet
STATUS_FILTERS = {
    "incomplete": Q(onboardingComplete=False),
    "pending": Q(onboardingComplete=True, verification_status="PENDING"),
    "approved": Q(verification_status="APPROVED"),
    "rejected": Q(verification_status="REJECTED"),
}


def notify_artisan(artisan, message, subject):
    Notification.objects.create(recipient=artisan, message=message, notification_type="VERIFICATION")
    try:
        send_mail(
            subject=subject,
            message=f"Hi {artisan.first_name},\n\n{message}\n\nThe Handwork Expert team",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[artisan.email],
            fail_silently=True,
        )
    except Exception as error:
        logger.error(f"Failed to send verification email to {artisan.email}: {error}")


class AdminArtisanViewSet(viewsets.ReadOnlyModelViewSet):
    """Staff review of artisans: list by status, inspect, approve or reject."""

    permission_classes = [IsAdminUser]
    serializer_class = AdminArtisanSerializer

    def get_queryset(self):
        queryset = (
            User.objects.filter(role="ARTISAN")
            .select_related("verified_by", "bankdetails")
            .prefetch_related(Prefetch("skill_set", queryset=Skill.objects.prefetch_related("services")))
            .order_by("-date_joined")
        )
        status_filter = STATUS_FILTERS.get(self.request.query_params.get("status", ""))
        return queryset.filter(status_filter) if status_filter is not None else queryset

    @action(detail=False, methods=["get"])
    def counts(self, request):
        counts = User.objects.filter(role="ARTISAN").aggregate(
            **{name: Count("pk", filter=condition) for name, condition in STATUS_FILTERS.items()}
        )
        return response.Response(counts)

    def _review(self, artisan, *, status_value, note=""):
        artisan.verification_status = status_value
        artisan.verification_note = note
        artisan.verified_at = timezone.now()
        artisan.verified_by = self.request.user
        artisan.save(update_fields=["verification_status", "verification_note", "verified_at", "verified_by"])
        logger.info(f"Artisan {artisan.email} {status_value.lower()} by {self.request.user.email}")
        return response.Response(self.get_serializer(artisan).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        artisan = self.get_object()
        if not artisan.onboardingComplete:
            return response.Response(
                {"detail": "This artisan hasn't finished onboarding yet."}, status=status.HTTP_400_BAD_REQUEST
            )
        result = self._review(artisan, status_value="APPROVED")
        notify_artisan(
            artisan,
            "Your profile has been approved. Customers can now find and book you on Handwork Expert.",
            "You're approved on Handwork Expert",
        )
        return result

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        artisan = self.get_object()
        serializer = RejectArtisanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        result = self._review(artisan, status_value="REJECTED", note=reason)
        notify_artisan(
            artisan,
            f"We couldn't approve your profile yet: {reason} Please update your details in the app and we'll review them again.",
            "Action needed on your Handwork Expert profile",
        )
        return result
