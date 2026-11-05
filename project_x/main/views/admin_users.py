"""
Staff view of every account. Deactivating blocks sign-in and hides an artisan from customers; it keeps the
account's data and can be undone. Staff accounts are managed in Django admin, not here.
"""
import logging

from django.db.models import Count, Q
from rest_framework import response, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser

from chat.messaging import CLOSED_STATUSES
from main.emails import send_email
from main.models import User
from main.serializers.admin_serializers import AdminUserSerializer, RejectArtisanSerializer

logger = logging.getLogger('main')

USER_FILTERS = {
    "customers": Q(role="USER", is_staff=False, is_active=True),
    "artisans": Q(role="ARTISAN", is_staff=False, is_active=True),
    "staff": Q(is_staff=True),
    "deactivated": Q(is_active=False, is_staff=False),
}

OPEN_AS_CUSTOMER = ~Q(job_owner__status__in=CLOSED_STATUSES) & Q(job_owner__isnull=False)
OPEN_AS_ARTISAN = ~Q(bookartisan__status__in=CLOSED_STATUSES) & Q(bookartisan__isnull=False)


class AdminUserViewSet(viewsets.ReadOnlyModelViewSet):
    """Every account by type, plus `deactivate` and `reactivate`"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminUserSerializer

    def get_queryset(self):
        queryset = User.objects.annotate(
            bookings=Count("job_owner", distinct=True) + Count("bookartisan", distinct=True),
            open_bookings=(
                Count("job_owner", filter=OPEN_AS_CUSTOMER, distinct=True)
                + Count("bookartisan", filter=OPEN_AS_ARTISAN, distinct=True)
            ),
        ).order_by("-date_joined")
        user_filter = USER_FILTERS.get(self.request.query_params.get("status", ""))
        return queryset.filter(user_filter) if user_filter is not None else queryset

    @action(detail=False, methods=["get"])
    def counts(self, request):
        return response.Response(User.objects.aggregate(
            **{name: Count("pk", filter=condition) for name, condition in USER_FILTERS.items()}
        ))

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        if user.is_staff:
            return response.Response(
                {"detail": "Staff accounts are managed in Django admin."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not user.is_active:
            return response.Response({"detail": "This account is already deactivated."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = RejectArtisanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        user.is_active = False
        user.save(update_fields=["is_active"])
        logger.info(f"Account {user.email} deactivated by {request.user.email}: {reason}")
        send_email(
            user.email,
            "Your Handwork Expert account has been deactivated",
            f"Hi {user.first_name},\n\nOur team has deactivated your account, so you can't sign in for now.\n\n"
            f"Reason: {reason}\n\nIf you think this is a mistake, please contact support.\n\nThe Handwork Expert team",
        )
        return response.Response(self.get_serializer(user).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        user = self.get_object()
        if user.is_active:
            return response.Response({"detail": "This account is already active."}, status=status.HTTP_400_BAD_REQUEST)

        user.is_active = True
        user.save(update_fields=["is_active"])
        logger.info(f"Account {user.email} reactivated by {request.user.email}")
        send_email(
            user.email,
            "Your Handwork Expert account is active again",
            f"Hi {user.first_name},\n\nYour account has been reactivated, so you can sign in again.\n\nThe Handwork Expert team",
        )
        return response.Response(self.get_serializer(user).data)
