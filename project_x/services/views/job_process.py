from . import BookArtisan, ArtisanBookingSerializers
from rest_framework import viewsets, response, status


class BookArtisanViewSet(viewsets.ModelViewSet):
    queryset = BookArtisan.objects.select_related('client', 'artisan').prefetch_related('services')
    serializer_class = ArtisanBookingSerializers

