from rest_framework import serializers
from . import Services, User, Category


class ServiceSerializers(serializers.ModelSerializer):
    client = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), many=True)
    class Meta:
        model = Services
        fields = ("id", "client", "name", "description", "active")


class CategorySerializers(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "service")

    def to_representation(self, instance):
       representation = super().to_representation(instance)
       representation['service'] = [service_category.name for service_category in instance.service.all()]
       return representation