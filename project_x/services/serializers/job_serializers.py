from rest_framework import serializers
from . import BookArtisan


class ArtisanBookingSerializers(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = BookArtisan
        fields = ('artisan', 'client', 'services', 'description', 
        'budget', 'created_at', 'updated_at', 'address', 'date', 'time', 'status')

