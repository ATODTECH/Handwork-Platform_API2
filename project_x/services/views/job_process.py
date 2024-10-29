from . import BookArtisan, ArtisanBookingSerializers
from rest_framework import viewsets, response, status
from rest_framework.decorators import api_view
from django.shortcuts import get_list_or_404
from django.db.models import Q


class BookArtisanViewSet(viewsets.ModelViewSet):
    queryset = BookArtisan.objects.select_related('client', 'artisan').prefetch_related('services')
    serializer_class = ArtisanBookingSerializers


@api_view(['GET'])
def get_my_bookings(request):
    # Get the current user
    user = request.user

    # Retrieve bookings for either artisan or client
    bookings = get_list_or_404(BookArtisan, Q(artisan=user) | Q(client=user))

    # Serialize the bookings
    serialized_data = ArtisanBookingSerializers(bookings, many=True).data
    
    # Return the serialized data with a successful response
    return response.Response(serialized_data, status=status.HTTP_200_OK) 