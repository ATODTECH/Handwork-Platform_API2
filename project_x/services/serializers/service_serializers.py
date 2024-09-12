from rest_framework import serializers
from . import Services, Category, BookService


class ServiceSerializers(serializers.ModelSerializer):
    class Meta:
        model = Services
        fields = ("name", "description", "active")


class ServiceCategorySerializers(serializers.ModelSerializer):
    # message = serializers.CharField(read_only=True)
    class Meta:
        model = Category
        fields = "__all__"


class BookService(serializers.ModelSerializer):
    class Meta:
        model = BookService
        fields = ("artisan", "client", "service", "renumeration", "completed")
