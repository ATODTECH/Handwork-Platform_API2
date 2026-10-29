from rest_framework import serializers
from . import BookArtisan, Skill, Rating, RatingSerializers
from services.models.job_models import MaterialQuote
from django.shortcuts import get_list_or_404
from django.utils import timezone
# from datetime import datetime

# Paid and not yet settled: the customer and artisan see each other's phone number to arrange the job
CONTACT_STATUSES = ("Paid", "Deposit Paid", "In Progress", "Completed", "Disputed")


class ArtisanBookingSerializers(serializers.ModelSerializer):
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)  # New field for combined date & time

    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField(format="%H:%M:%S", input_formats=["%H:%M", "%H:%M:%S"])
    
    class Meta:
        model = BookArtisan
        fields = ('id', 'artisan', 'client', 'services', 'description', 'budget', 'agreed_amount', 'created_at',
            'updated_at', 'address', 'scheduled_date', 'scheduled_time', 'status', 'completed_at',
            'fully_paid_at', 'released_at', 'cancelled_at', 'cancellation_reason', 'disputed_at', 'dispute_reason',
            'resolved_at', 'resolution_note',
        )
        # The customer is whoever is signed in, never taken from the request body; the rest move with the status
        read_only_fields = ('client', 'completed_at', 'fully_paid_at', 'released_at', 'cancelled_at', 'cancellation_reason',
                            'disputed_at', 'dispute_reason', 'resolved_at', 'resolution_note')

    def validate_scheduled_date(self, scheduled_date):
        if self.instance is None and scheduled_date < timezone.localdate():
            raise serializers.ValidationError("Choose today or a later date.")
        return scheduled_date

    def validate_artisan(self, artisan):
        # Only artisans staff have approved can take bookings
        if artisan and artisan.verification_status != "APPROVED":
            raise serializers.ValidationError("This artisan isn't available for bookings yet.")
        return artisan

    def validate(self, attrs):
        artisan, services = attrs.get('artisan'), attrs.get('services')
        if artisan and services:
            offered = set(Skill.objects.filter(artisan=artisan).values_list('services', flat=True)) - {None}
            if offered and not {service.id for service in services} <= offered:
                raise serializers.ValidationError({"services": "This artisan doesn't offer all of these services."})
        return attrs

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['artisan'] = f"{instance.artisan.first_name} {instance.artisan.last_name}"
        representation['artisan_location'] = {"longitude": instance.artisan.longitude, 
                                      "latitude": instance.artisan.latitude, 
                                      "address": instance.artisan.address}
        representation['client'] = f"{instance.client.first_name} {instance.client.last_name}"
        representation['services'] = [service.name for service in instance.services.all()]
        representation['rating'] = self.get_artisan_rating(instance.artisan)
        representation['contact'] = self.get_contact(instance)
        return representation

    def get_contact(self, booking):
        """The other side's phone number, once the job is paid for and they need to arrange it"""
        request = self.context.get('request')
        if booking.status not in CONTACT_STATUSES or request is None:
            return None
        if request.user == booking.client and booking.artisan:
            return booking.artisan.contact
        if request.user == booking.artisan:
            return booking.client.contact
        return None

    def get_artisan_rating(self, artisan):
        ratings = Rating.objects.filter(artisan=artisan)
        if ratings.exists():
            return RatingSerializers(ratings, many=True).data
        return None
    
class MaterialQuoteSerializer(serializers.ModelSerializer):
    artisan = serializers.ReadOnlyField(source='artisan.get_full_name', read_only=True)
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

        if booking.status not in ["Accepted", "Paid", "Deposit Paid", "In Progress"]:
            raise serializers.ValidationError("Material funds can only be requested after booking is accepted.")

        if data['total_cost'] <= 0:
            raise serializers.ValidationError("Total cost must be greater than zero.")

        return data

class CancelBookingSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True, required=False, allow_blank=True)


class ReportProblemSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=10, max_length=2000, trim_whitespace=True)


class ResolveDisputeSerializer(serializers.Serializer):
    # release pays the artisan; refund returns the customer's payment
    outcome = serializers.ChoiceField(choices=("release", "refund"))
    note = serializers.CharField(max_length=2000, trim_whitespace=True)


class AdminBookingSerializer(serializers.ModelSerializer):
    """A booking with everything staff need to follow it or settle a problem"""
    client = serializers.SerializerMethodField()
    artisan = serializers.SerializerMethodField()
    services = serializers.SerializerMethodField()
    resolved_by = serializers.SerializerMethodField()

    class Meta:
        model = BookArtisan
        fields = ('id', 'client', 'artisan', 'services', 'description', 'budget', 'agreed_amount', 'address',
                  'scheduled_date', 'scheduled_time', 'status', 'created_at', 'updated_at', 'fully_paid_at',
                  'completed_at', 'released_at', 'payment_reference', 'cancelled_at', 'cancellation_reason',
                  'disputed_at', 'dispute_reason', 'resolved_at', 'resolved_by', 'resolution_note')
        read_only_fields = fields

    @staticmethod
    def person(user):
        if user is None:
            return None
        return {
            "id": str(user.id),
            "name": f"{user.first_name} {user.last_name}".strip() or user.email,
            "email": user.email,
            "contact": user.contact,
        }

    def get_client(self, booking):
        return self.person(booking.client)

    def get_artisan(self, booking):
        return self.person(booking.artisan)

    def get_services(self, booking):
        return [service.name for service in booking.services.all()]

    def get_resolved_by(self, booking):
        reviewer = booking.resolved_by
        return (f"{reviewer.first_name} {reviewer.last_name}".strip() or reviewer.email) if reviewer else None
