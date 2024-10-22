from rest_framework import serializers
from . import Services, User, Category, Skill, UserSerializer
from django.shortcuts import get_object_or_404


class ServiceSerializers(serializers.ModelSerializer):
    client = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    class Meta:
        model = Services
        fields = ("id", "client", "name", "description", "active")
        
    def to_representation(self, instance):
        representation = super(ServiceSerializers, self).to_representation(instance)
        
        # Get related artisans and their data
        artisans_data = self.get_related_artisans(instance.id)
        representation['client'] = artisans_data

        # Add artisan skill description (if client is an artisan)
        # representation['skill'] = self.artisan_skill(instance.client)
        return representation

    def get_related_artisans(self, instance):
        # Get all skills related to the service using the ManyToMany field
        skills = Skill.objects.filter(services=instance)
        
        # Collect all artisans associated with the skills
        artisans = [
                {
                    "artisan": f"{skill.artisan.first_name} {skill.artisan.last_name}",
                    "about_me": skill.description,
                    "price_range": f"{skill.base_charge} - {skill.cap_charge}",
                    "address": skill.artisan.address,
                    "longitude": skill.artisan.longitude,
                    "latitude": skill.artisan.latitude,
                    "services": [service.name for service in skill.services.all()],  # Collect service names in a list
                    "workdays": {"weekdays_and_weekends": skill.weekdays_and_weekends, 
                                 "weekdays_only": skill.weekdays_only, 
                                 "weekends_only": skill.weekends_only
                                 }
                }   
                    for skill in skills
                ]
        
        if not artisans:
            return []

        return artisans    


class CategorySerializers(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "description", "service")

    def to_representation(self, instance):
       representation = super().to_representation(instance)
       representation['service'] = [{'id': service_category.id, 'name':service_category.name} for service_category in instance.service.all()]
       return representation