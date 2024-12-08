from . import BookArtisan, ArtisanBookingSerializers
from rest_framework import viewsets, response, status, filters
from rest_framework.decorators import api_view
from django.shortcuts import get_list_or_404
from django.db.models import Q
from setup.processors.filters import DateSearchFilter


class BookArtisanViewSet(viewsets.ModelViewSet):
    queryset = BookArtisan.objects.select_related('client', 'artisan').prefetch_related('services')
    serializer_class = ArtisanBookingSerializers



@api_view(['GET'])
def get_my_bookings(request):
    # Get the current user
    user = request.user

    # Retrieve bookings for either artisan or client
    try:
        bookings = BookArtisan.objects.filter(artisan=user, client=user).first()
        serialized_data = ArtisanBookingSerializers(bookings, many=True).data
    except BookArtisan.DoesNotExist:
        return None    
    
    # Return the serialized data with a successful response
    return response.Response(serialized_data, status=status.HTTP_200_OK) 


class MyFilteredBookings(viewsets.ModelViewSet):
    serializer_class = ArtisanBookingSerializers
    filter_backends = [DateSearchFilter]

    def get_queryset(self):
        user = self.request.user
        try:
            bookings = BookArtisan.objects.filter(Q(status="Accepted"), (Q(client=user) | Q(artisan=user))).select_related("client", "artisan").prefetch_related("services")
        except BookArtisan.DoesNotExist:
            return None
        
        return bookings
    
    def retrieve(self, request):
        user = self.request.user
        booking_id = request.query_params.get("booking_id")
        return BookArtisan.objects.filter(artisan=user, id=booking_id).first()