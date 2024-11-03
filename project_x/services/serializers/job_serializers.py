from rest_framework import serializers
from . import BookArtisan, Skill, Rating, RatingSerializers
from django.shortcuts import get_list_or_404
# from datetime import datetime


class ArtisanBookingSerializers(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)  # New field for combined date & time

    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField(format="%H:%M:%S", input_formats=["%H:%M", "%H:%M:%S"])
    
    class Meta:
        model = BookArtisan
        fields = ('id', 'artisan', 'client', 'services', 'description', 'budget', 'created_at', 
            'updated_at', 'address', 'scheduled_date', 'scheduled_time', 'status',
        )

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['artisan'] = f"{instance.artisan.first_name} {instance.artisan.last_name}"
        representation['artisan_location'] = {"longitude": instance.artisan.longitude, 
                                      "latitude": instance.artisan.latitude, 
                                      "address": instance.artisan.address}
        representation['client'] = f"{instance.client.first_name} {instance.client.last_name}"
        representation['services'] = [service.name for service in instance.services.all()]
        representation['rating'] = self.get_artisan_rating(instance.artisan)
        return representation
    
    def get_artisan_rating(self, artisan_id):
        ratings = Rating.objects.filter(client=artisan_id)
        if ratings.exists():
            return RatingSerializers(ratings, many=True).data
        return None