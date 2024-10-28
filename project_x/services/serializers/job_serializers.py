from rest_framework import serializers
from . import BookArtisan, Skill
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

    # def validate(self, attrs):
    #     artisan = attrs.get('artisan')
    #     required_services = attrs.get('services')

    #     # Get Artisan Services
    #     services = Skill.objects.filter(artisan=artisan).select_related("artisan")

    #     # Check if the artisan offers each required service
    #     # for service in required_services:
    #     #     if not any(service in skilled_services.services.all() for skilled_services in services):
    #     #         raise serializers.ValidationError({"error": f"This Artisan does not perform this service '{service.name}'. "})
    #     # return attrs

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['services'] = [service.name for service in instance.services.all()]
        return representation