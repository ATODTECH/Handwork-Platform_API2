from rest_framework import serializers
from . import Services, User, Category, Skill, UserSerializer, Rating
from django.shortcuts import get_object_or_404
from django.db.models import Avg
from django.db.models import Prefetch

class ServiceSerializers(serializers.ModelSerializer):
    client = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    class Meta:
        model = Services
        fields = ("id", "client", "name", "description", "active")
        
    def to_representation(self, instance):
        representation = super(ServiceSerializers, self).to_representation(instance)
        
        # Get related artisans and their data, including their rating
        artisans_data = self.get_related_artisans(instance)
        representation['client'] = artisans_data

        return representation

    def get_related_artisans(self, instance):
        # Prefetch related skills and ratings to reduce the number of queries
        # Only artisans staff have approved are offered to customers
        skills_with_artisans = Skill.objects.filter(
            services=instance, artisan__verification_status="APPROVED", artisan__is_active=True
        ).select_related(
            'artisan'
        ).prefetch_related(
            'services'
        )

        # Get all artisan ratings at once
        ratings = (
            Rating.objects
            .filter(artisan__in=[skill.artisan for skill in skills_with_artisans])
            .values('artisan')
            .annotate(average_rating=Avg('star'))
        )

        # Map artisan IDs to their average ratings
        artisan_ratings = {rating['artisan']: rating['average_rating'] for rating in ratings}

        artisans = []
        for skill in skills_with_artisans:
            artisan = skill.artisan
            # Retrieve the artisan's average rating from pre-fetched ratings
            average_rating = artisan_ratings.get(artisan.id, 0)

            # Build the artisan data with the average rating included
            artisan_data = {
                "artisan_id": artisan.id,
                "first_name": artisan.first_name,
                "last_name": artisan.last_name,
                "description": skill.description,
                "base_charge": skill.base_charge,
                "cap_charge": skill.cap_charge,
                "address": artisan.address,
                "contact": artisan.contact,
                "longitude": artisan.longitude,
                "latitude": artisan.latitude,
                "services": [{"id": service.id, "name": service.name} for service in skill.services.all()],
                "workdays": {
                    "weekdays_and_weekends": skill.weekdays_and_weekends,
                    "weekdays_only": skill.weekdays_only,
                    "weekends_only": skill.weekends_only
                },
                "average_rating": round(average_rating, 2)  # Add average rating to artisan data
            }
            artisans.append(artisan_data)

        return artisans if artisans else []
    
    
class CategorySerializers(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "description", "service")

    def to_representation(self, instance):
       representation = super().to_representation(instance)
       representation['service'] = [{'id': service_category.id, 'name':service_category.name} for service_category in instance.service.all() if service_category.active]
       return representation


class AdminServiceSerializer(serializers.ModelSerializer):
    """A service as staff manage it, with what uses it"""

    categories = serializers.PrimaryKeyRelatedField(
        source="service_categories", queryset=Category.objects.all(), many=True, required=False
    )
    # Annotated by the view; a service that was just created has neither yet
    artisans = serializers.IntegerField(read_only=True, default=0)
    bookings = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Services
        fields = ("id", "name", "description", "active", "categories", "artisans", "bookings")

    def validate_name(self, value):
        others = Services.objects.filter(name__iexact=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError("There's already a service with this name.")
        return value
