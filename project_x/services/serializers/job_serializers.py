from rest_framework import serializers
from . import BookArtisan, Skill, Rating, RatingSerializers
from services.models.job_models import MaterialQuote
from django.shortcuts import get_list_or_404
# from datetime import datetime


class ArtisanBookingSerializers(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)  # New field for combined date & time

    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField(format="%H:%M:%S", input_formats=["%H:%M", "%H:%M:%S"])
    
    class Meta:
        model = BookArtisan
        fields = ('id', 'artisan', 'client', 'services', 'description', 'budget', 'agreed_amount', 'created_at', 
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
    
class MaterialQuoteSerializer(serializers.ModelSerializer):
    artisan = serializers.StringField(source='artisan.get_full_name', read_only=True)
    booking_id = serializers.IntegerField(source='booking.id', read_only=True)
    status = serializers.CharField(read_only=True)
    requested_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = MaterialQuote
        fields = [
            'id', 'booking', 'booking_id', 'artisan',
            'job_title', 'material_list', 'total_cost',
            'job_description', 'requested_at', 'status',
            'admin_note'
        ]
        read_only_fields = ['status', 'requested_at', 'admin_note']

    def validate(self, data):
        request = self.context['request']
        booking = data['booking']

        # Security checks
        if booking.artisan != request.user:
            raise serializers.ValidationError("You can only request funds for your own bookings.")

        if booking.status not in ["Accepted", "Deposit Paid", "In Progress"]:
            raise serializers.ValidationError("Material funds can only be requested after booking is accepted.")

        if data['total_cost'] <= 0:
            raise serializers.ValidationError("Total cost must be greater than zero.")

        return data