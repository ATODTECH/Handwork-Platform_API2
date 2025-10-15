from main.models import User, Skill, BankDetails, OnboardingLevel, Rating, Notification, SkillImage
from services.models.services_models import Services
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import get_object_or_404, Http404
import re
import logging
import json
import ast
import cloudinary.uploader
from django.core.validators import FileExtensionValidator

logger = logging.getLogger('main')


class UserSerializer(serializers.ModelSerializer):
    onboardingComplete = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = User
        fields = ('id', 'first_name', 'last_name', 'email', 'contact', 'profile_image', 'gender', 'address', 'state', 'city', 'country', 'longitude', 'latitude', 'role', 'onboardingComplete')
        
    def validate(self, attrs):
        password = attrs.get("password")
        email = attrs.get("email")

        if password:
            # Validate the password using regular expressions
            if not re.match(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', password):
                raise serializers.ValidationError(
                    {"error": "Passwords must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."})
            if len(password) <= 5:
                raise serializers.ValidationError("Passwords must be more than 5 characters.")
            
            # Validate Email
            if User.objects.filter(email=email).exists():
                raise serializers.ValidationError({"error": "Sorry this email is taken!"})
        return attrs
    
    def validate_email(self, value):
        """
        Check that the email is unique.
        """
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already registered.")
        return value

    def create(self, validated_data):
        # Extract password from validated_data
        password = validated_data.pop("password", None)

        instance = User.objects.create(**validated_data)
        if password:
            # Set and save the user's password only if a valid password is provided
            instance.set_password(password)
            instance.save()
        return instance

class SkillImageSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png'])],
        required=True
    )
    
    class Meta:
        model = SkillImage
        fields = ('id', 'image')

    def create(self, validated_data):
        try:
            image = validated_data['image']
            logger.info(f"Uploading image {image.name} to Cloudinary")
            upload_result = cloudinary.uploader.upload(
                image,
                folder="artisan/skill_images",
                resource_type="image",
                allowed_formats=['jpg', 'jpeg', 'png']
            )
            validated_data['image'] = upload_result['secure_url']
            instance = super().create(validated_data)
            logger.info(f"SkillImage created with URL: {instance.image.url}")
            return instance
        except Exception as e:
            logger.error(f"Failed to upload image {image.name} to Cloudinary: {str(e)}")
            raise serializers.ValidationError({"images": f"Failed to upload image: {str(e)}"})

class SkillSerializer(serializers.ModelSerializer):
    artisan = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    services = serializers.PrimaryKeyRelatedField(
        queryset=Services.objects.all(),
        many=True,
        write_only=True,
        required=False
    )
    images = SkillImageSerializer(many=True, required=False, read_only=False)
    
    class Meta:
        model = Skill
        fields = (
            "id", "artisan", "services", "description", "base_charge",
            "cap_charge", "weekdays_only", "weekends_only",
            "weekdays_and_weekends", "images"
        )
    
    def to_internal_value(self, data):
        services_input = data.get('services', '')
        logger.info(f"Raw services input: {services_input} (type: {type(services_input)})")

        services = []
        if services_input:
            if isinstance(services_input, list):
                services = services_input
            else:
                try:
                    services = json.loads(services_input)
                except (json.JSONDecodeError, TypeError):
                    try:
                        services = ast.literal_eval(services_input)
                    except Exception:
                        raise serializers.ValidationError(
                            {"services": "Must be a valid array of service IDs, e.g., [1, 2]"}
                        )

            # Normalize to a flat list of ints
            if isinstance(services, (int, str)):
                services = [services]
            elif not isinstance(services, list):
                raise serializers.ValidationError({"services": "Must be an array of service IDs"})

            try:
                services = [int(s) for s in services]
            except (ValueError, TypeError):
                raise serializers.ValidationError({"services": "All service IDs must be valid integers"})

        # 🟢 Convert each item to a string so DRF parses it correctly as pk values
        data = data.copy()
        data.setlist('services', [str(s) for s in services])  # important
        logger.info(f"Parsed services for DRF: {data.getlist('services')} (types: {[type(s) for s in data.getlist('services')]})")

        return super().to_internal_value(data)
    
    def validate(self, attrs):
        images = self.context['request'].FILES.getlist('images', [])
        if images:
            for image in images:
                if image.content_type not in ['image/jpeg', 'image/png']:
                    raise serializers.ValidationError({"images": "Only JPG and PNG files are allowed."})
        return attrs
    
    def create(self, validated_data):
        artisan = self.context['request'].user
        services = validated_data.pop('services', [])  # will be list of Services instances
        images = self.context['request'].FILES.getlist('images', [])

        instance = Skill.objects.create(artisan=artisan, **validated_data)

        if services:
            # services are instances — set them on m2m
            instance.services.set(services)

        for image in images:
            SkillImage.objects.create(skill=instance, image=image)

        logger.info(f"Skill created for artisan {artisan.email}: {instance.description} with {len(images)} images")
        return instance

    
    def update(self, instance, validated_data):
        services = validated_data.pop('services', None)
        images = self.context['request'].FILES.getlist('images', [])
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if services is not None:
            instance.services.set(Services.objects.filter(id__in=services))
        
        if images:
            instance.images.all().delete()  # Optional: Delete existing images
            for image in images:
                SkillImage.objects.create(skill=instance, image=image)
        
        logger.info(f"Skill updated for artisan {instance.artisan.email}: {instance.description} with {len(images)} images")
        return instance
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['artisan'] = f"{instance.artisan.first_name} {instance.artisan.last_name}"
        representation['services'] = [{"id": service.id, "name": service.name} for service in instance.services.all()]
        representation['images'] = [{"id": image.id, "url": image.image.url} for image in instance.images.all()]
        return representation

class BankDetailSerializer(serializers.ModelSerializer):
    artisan =serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    class Meta:
        model = BankDetails
        fields = ("id", "artisan", "name", "account_name", "account_number", "payment_on_delivery", "escrow_payment")
    
    def create(self, validated_data):
        artisan = self.context['request'].user
        instance = BankDetails(artisan=artisan, **validated_data)
        instance.save()
        return instance


class ArtisanSerializer(serializers.ModelSerializer):
    onboardingComplete = serializers.BooleanField(read_only=True)
    isAvailable = serializers.BooleanField(read_only=True)
    class Meta:
        model = User
        fields = ("id", "first_name", "last_name", "email", "contact", 'profile_image', "state", "city", "nin", "bvn", "document", "address", "document_type", "longitude", "latitude", "role", "onboardingComplete", "password", "isAvailable")
        
    extra_kwargs = {
            'nin': {'required': False},
            'profile_image': {'required': False},
            'bvn': {'required': False},
            'document': {'required': False},
            'document_type': {'required': False},
            'role': {'required': False},
            'state': {'required': False},
            'onboardingComplete': {'required': False},
            'isAvailable': {'required': False}
        }
    
    def validate(self, attrs):
        email = attrs.get("email")
        if User.objects.filter(email=email).exists():
                raise serializers.ValidationError({"error": "Sorry this email is taken!"})
        return attrs

    def create(self, validated_data):
        # Extract password from validated_data
        password = validated_data.pop("password", None)
        if password:
            # Validate the password using regular expressions
            if not re.match(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', password):
                raise serializers.ValidationError(
                    {"error": "Passwords must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."})

        instance = User.objects.create(**validated_data)
        if password:
            # Set and save the user's password only if a valid password is provided
            instance.set_password(password)
            instance.save()
        return instance
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['skill'] = self.get_artisan_skill(instance)
        representation['rating'] = self.get_artisan_rating(instance)
        return representation
    
    def get_artisan_skill(self, artisan):
        # Try to get the Skill instance
        skill = Skill.objects.filter(artisan=artisan).select_related('artisan').first()
        
        # If no skill found, return None
        if skill is None:
            return None
        
        # Otherwise, return the serialized skill data
        return SkillSerializer(skill).data
    
    def get_artisan_rating(self, artisan):
        rating = Rating.objects.filter(artisan=artisan).select_related('artisan', 'client').first()

        if rating is None:
            return None
        
        return RatingSerializers(rating).data
        

class OnboardingLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = OnboardingLevel
        fields = ("level_1", "level_2", "level_3")


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

        onboarding_data = self.get_user_onboarding_level(self.user)
        
        # Add custom data to the response
        data.update({
            "id": user_id,
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "name": f"{self.user.first_name} {self.user.last_name}",
            "role": self.user.role,
            "longitude": self.user.longitude,
            "latitude": self.user.latitude,
            "onboardingComplete": self.user.onboardingComplete,
            "onboarding_level": onboarding_data
        })
        
        return data
    
    def get_user_onboarding_level(self, user):
        try:
            onboarding = OnboardingLevel.objects.get(user=user)
        except OnboardingLevel.DoesNotExist:
            return []
        return OnboardingLevelSerializer(onboarding).data
    

class RatingSerializers(serializers.ModelSerializer):
    class Meta:
        model = Rating
        fields = ("id", "client", "artisan", "star", "message")
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['client'] = f"{instance.client.first_name} {instance.client.last_name}"
        representation['artisan'] = f"{instance.artisan.first_name} {instance.artisan.last_name}"
        return representation
    
class NotificationSerializer(serializers.ModelSerializer):
    recipient = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    
    class Meta:
        model = Notification
        fields = ('id', 'recipient', 'message', 'notification_type', 'created_at', 'is_read')
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['recipient'] = f"{instance.recipient.first_name} {instance.recipient.last_name}"
        return representation