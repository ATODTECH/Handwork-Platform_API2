from main.models import User
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import get_object_or_404, Http404
import re


class UserSerializer(serializers.ModelSerializer):
    # message = serializers.CharField(read_only=True)
    class Meta:
        model = User
        fields = ("id", "first_name", "last_name", "email", "contact", "password")

    def create(self, validated_data):
        # Extract password from validated_data
        password = validated_data.pop("password", None)
        
        if password:
            # Validate the password using regular expressions
            if not re.match(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', password):
                raise ValidationError(
                    {"error": "Passwords must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."})

        instance = User.objects.create(**validated_data)
        if password:
            # Set and save the user's password only if a valid password is provided
            instance.set_password(password)
            instance.save()
        return instance



class ArtisanSerializer(serializers.ModelSerializer):
    # message = serializers.CharField(read_only=True)
    class Meta:
        model = User
        fields = ("id", "first_name", "last_name", "email", "contact", "state", "city", "nin", "bvn", "document", "document_type", "role", "password")

    def create(self, validated_data):
        # Extract password from validated_data
        password = validated_data.pop("password", None)
        if password:
            # Validate the password using regular expressions
            if not re.match(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', password):
                raise ValidationError(
                    {"error": "Passwords must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."})

        instance = User.objects.create(**validated_data)
        if password:
            # Set and save the user's password only if a valid password is provided
            instance.set_password(password)
            instance.save()
        return instance
    
    # def to_representation(self, instance):
    #     representation = super(ArtisanSerializer, self).to_representation(instance)
    #     representation['document'] = instance.document if instance.document else None
    #     return representation


class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        # Obtain the original token from the superclass
        token = super().get_token(user)
        
        # Add custom claims to the token
        token['email'] = user.email
        return token
    
    def validate(self, attrs):
        # Validate the incoming data using the superclass method
        data = super().validate(attrs)
        
        # Get the user's ID and generate the refresh token
        user_id = self.user.id
        refresh = self.get_token(self.user)
        
        # Add custom data to the response
        data.update({
            "id": user_id,
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "name": f"{self.user.first_name} {self.user.last_name}"
        })
        
        return data



# class ArtisanProfileSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = ArtisanProfile
#         fields = "__all__"
        
#     def to_representation(self, instance):
#         representation = super(ArtisanProfileSerializer, self).to_representation(instance)
#         representation["service_type"] = [service.name for service in instance.service_type.all()]
#         return representation


# class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
#     def validate(self, attrs):
#         contact = attrs.get("contact")
#         password = attrs.get("password")

#         user = authenticate(request=self.context.get("request"), contact=contact, password=password)

#         if not user:
#             raise AuthenticationFailed(
#                 'No active account found with the given credentials'
#             )
#         return super().validate(attrs)