"""
Staff management of the service catalogue. Hiding a service stops new artisans choosing it and takes it out of
the category lists; artisans who already offer it keep it. A service in use can only be hidden, not deleted.
"""
from django.db.models import Count
from rest_framework import response, status, viewsets
from rest_framework.permissions import IsAdminUser

from services.models.services_models import Services
from services.serializers.service_serializers import AdminServiceSerializer


class AdminServiceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    serializer_class = AdminServiceSerializer

    def get_queryset(self):
        return (
            Services.objects.annotate(
                artisans=Count("artisan_services", distinct=True),
                bookings=Count("booked_service", distinct=True),
            )
            .prefetch_related("service_categories")
            .order_by("name")
        )

    def destroy(self, request, *args, **kwargs):
        service = self.get_object()
        if service.artisans or service.bookings:
            return response.Response(
                {"detail": "Artisans or bookings use this service, so it can't be deleted. Hide it instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        service.delete()
        return response.Response(status=status.HTTP_204_NO_CONTENT)
