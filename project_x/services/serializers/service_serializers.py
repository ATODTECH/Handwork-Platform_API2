from rest_framework import serializers
from . import Services, Category


class ServiceSerializers(serializers.ModelSerializer):
    class Meta:
        model = Services
        fields = ("name", "description", "active")
        
    # def to_representation(self, instance):
    #     representation = super(ServiceSerializers, self).to_representation(instance)
    #     representation["status"] = True
    #     representation["message"] = "Success"
    #     # representation["services"] = [{"id": service.id, "name":service.name} for service in instance.services.all()]
    #     return representation


class ServiceCategorySerializers(serializers.ModelSerializer):
    message = serializers.CharField(read_only=True)
    class Meta:
        model = Category
        fields = "__all__"
        
    def to_representation(self, instance):
        representation = super(ServiceCategorySerializers, self).to_representation(instance)
        representation["status"] = True
        representation["message"] = "Success"
        representation["services"] = [{"id": service.id, "name":service.name} for service in instance.services.all()]
        return representation