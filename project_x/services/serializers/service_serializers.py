from rest_framework import serializers
from . import Services, Category, BookService, User, SubCategory


class ServiceSerializers(serializers.ModelSerializer):
    client = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    class Meta:
        model = Services
        fields = ("id", "client", "name", "description", "active")


class ServiceCategorySerializers(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"
        
        
class ServiceSubcategorySerializers(serializers.ModelSerializer):
    services = ServiceCategorySerializers()
    class Meta:
        model = SubCategory
        fields = ("id", "name", "category", "services")
        



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