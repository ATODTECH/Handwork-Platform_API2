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
        # if instance.client:
        representation['client'] = self.get_related_artisans(instance.id)
        return representation

    def get_related_artisans(self, instance):
        # Get all skills related to the service using the ManyToMany field
        skills = Skill.objects.filter(services=instance)
        
        # Collect all artisans associated with the skills
        artisans = [skill.artisan for skill in skills]
        
        if not artisans:
            return []

        user_data = UserSerializer(artisans, many=True, context={'exclude_fields': ['password', 'role']}).data
        for user in user_data:
            user.pop('password')
        return user_data            


class CategorySerializers(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "service")

    def to_representation(self, instance):
       representation = super().to_representation(instance)
       representation['service'] = [service_category.name for service_category in instance.service.all()]
       return representation