from rest_framework import serializers
from . import Services, BookService, User, Category


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

class ServiceBookingSerializer(serializers.ModelSerializer):
    artisan = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role='ARTISAN'), required=False)
    client = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role='USER'), required=False)
    service = serializers.PrimaryKeyRelatedField(queryset=Services.objects.all())
    
    class Meta:
        model = BookService
        fields = ("id", "artisan", "client", "service", "renumeration", "completed")
        
    def create(self, validated_data):
        client = self.context['request'].user
        instance = BookService(client=client, **validated_data)
        instance.save()
        return instance