from rest_framework import serializers
from . import BookArtisan
# from datetime import datetime


class ArtisanBookingSerializers(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)  # New field for combined date & time

    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField(format="%H:%M:%S", input_formats=["%H:%M", "%H:%M:%S"])
    class Meta:
        model = BookArtisan
        fields = (
            'artisan', 'client', 'services', 'description', 'budget', 'created_at', 
            'updated_at', 'address', 'scheduled_date', 'scheduled_time', 'status',
        )
