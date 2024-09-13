from rest_framework import serializers
from . import Services, Category, BookService, User


class ServiceSerializers(serializers.ModelSerializer):
    class Meta:
        model = Services
        fields = ("id", "name", "description", "active")


class ServiceCategorySerializers(serializers.ModelSerializer):
    # message = serializers.CharField(read_only=True)
    class Meta:
        model = Category
        fields = "__all__"


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