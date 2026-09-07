from rest_framework import serializers
from .models import Contact, Subscribe
from django.utils.html import strip_tags


class ContactSerializer(serializers.ModelSerializer):
  class Meta:
    model = Contact
    fields = ['name', 'phoneNumber', 'email', 'message']

  def validate_name(self, value: str) -> str:
    value = value.strip()
    if not value:
      raise serializers.ValidationError("Name cannot be empty.")
    if len(value) > 100:
      raise serializers.ValidationError("Name is too long.")
    return strip_tags(value)

  def validate_phoneNumber(self, value: str) -> str:
    value = value.strip()
    if not value:
      raise serializers.ValidationError("Phone number cannot be empty.")
    if len(value) > 15:
      raise serializers.ValidationError("Phone number is too long.")
    return strip_tags(value)
  
  def validate_message(self, value: str) -> str:
    value = value.strip()
    if not value:
      raise serializers.ValidationError("Message cannot be empty.")
    if len(value) > 255:
      raise serializers.ValidationError("Message is too long.")
    return strip_tags(value)

  def create(self, validated_data: dict) -> Contact:
    return Contact.objects.create(**validated_data)


class SubscribeSerializer(serializers.ModelSerializer):
  class Meta:
    model = Subscribe
    fields = ['email']