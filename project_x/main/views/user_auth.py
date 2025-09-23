from main.models import User, Skill, BankDetails, Rating, Notification
from rest_framework.viewsets import ModelViewSet, ViewSet
from main.serializers.auth_serializers import (         UserSerializer, ArtisanSerializer, MyTokenObtainPairSerializer, SkillSerializer,
            BankDetailSerializer, RatingSerializers, NotificationSerializer)
from main.permissions import IsArtisanPermission
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import permissions, response, status, serializers
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import NotFound
from django.db import IntegrityError
from django.db.models import Count, Q
from rest_framework.decorators import api_view
from rest_framework.views import APIView
from django.core.exceptions import ValidationError
import re
from django.shortcuts import get_object_or_404
import logging

logger = logging.getLogger('main')

class SignUpUserView(ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            try:
                self.perform_create(serializer)
                user = User.objects.get(email=serializer.data['email'])
                Notification.objects.create(
                    recipient=user,
                    message=f"Welcome, {user.first_name}! Your account has been created.",
                    notification_type='SIGNUP'
                )
                # Send welcome email
                send_mail(
                    subject='Welcome to User Artisan!',
                    message=f"Hi {user.first_name},\n\nYour account has been created successfully.",
                    from_email='your.email@gmail.com',
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                logger.info(f"User created: {serializer.data['email']} (ID: {serializer.data['id']})")
            except IntegrityError as e:
                logger.error(f"Failed to create user: {str(e)}")
                if 'email' in str(e):
                    raise serializers.ValidationError({"email": "This email already exists."})
                raise serializers.ValidationError({"error": "An error occurred while saving the user."})
            headers = self.get_success_headers(serializer.data)
            return response.Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        logger.warning(f"Invalid user signup data: {serializer.errors}")
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ResetPassword(APIView):
    def post(self, request):
        email = request.data.get('email')
        new_password = request.data.get('new_password')
        if not email or not new_password:
            logger.warning("Password reset failed: Missing email or password")
            return response.Response({"error": "Email and new password are required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.error(f"Password reset failed: User not found for email {email}")
            return response.Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if len(new_password) < 8:
            logger.warning(f"Password reset failed for {email}: Password too short")
            raise ValidationError("Password must be at least 8 characters long.")
        if not re.search(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', new_password):
            logger.warning(f"Password reset failed for {email}: Invalid password format")
            raise ValidationError({"error": "Passwords must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."})
        user.set_password(new_password)
        user.save()
        Notification.objects.create(
            recipient=user,
            message=f"Your password has been successfully reset.",
            notification_type='PASSWORD_RESET'
        )
        logger.info(f"Password reset successful for {email}")
        return response.Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)

class SignInUserView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = MyTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            logger.info(f"User signed in: {request.data.get('email')}")
        else:
            logger.warning(f"Sign-in failed: {response.data}")
        return response


class SignUpArtisanViewSet(ModelViewSet):
    queryset = User.objects.filter(role="ARTISAN")
    serializer_class = ArtisanSerializer


class SkillViewSet(ViewSet):
    permission_classes = [IsArtisanPermission]
    
    def get_queryset(self):
        return Skill.objects.select_related("artisan")
    
    def create(self, request):
        serializer = SkillSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return response.Response(serializer.data, status=status.HTTP_201_CREATED)
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def list(self, request):
        skill = self.get_queryset()
        serializer = SkillSerializer(skill, many=True)
        return response.Response(serializer.data, status=status.HTTP_200_OK)
    
    def retrieve(self, request, pk=None):
        # Use get_object_or_404 to handle invalid pk and raise a 404 error if skill doesn't exist
        skill = get_object_or_404(self.get_queryset(), pk=pk)
        
        # Serialize the object
        serializer = SkillSerializer(skill)
        
        # Return the serialized data with a 200 OK status
        return response.Response(serializer.data, status=status.HTTP_200_OK)
    

class BankDetailViewSet(ViewSet):
    permission_classes = [IsArtisanPermission]
    
    def get_queryset(self):
        return BankDetails.objects.select_related("artisan")
    
    def create(self, request):
        serializer = BankDetailSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return response.Response(serializer.data, status=status.HTTP_201_CREATED)
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def list(self, request):
        queryset = self.get_queryset()
        serializer = BankDetailSerializer(queryset, many=True)
        return response.Response(serializer.data, status=status.HTTP_200_OK)
    
    def update(self, request, pk=None):
        try:
            bank_detail = self.get_queryset().get(pk=pk)
        except BankDetails.DoesNotExist:
            return response.Response(
                {"detail": "Bank detail not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        serializer = BankDetailSerializer(bank_detail, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return response.Response(serializer.data, status=status.HTTP_200_OK)
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RatingViewSet(ModelViewSet):
    queryset = Rating.objects.select_related("artisan", "client")
    serializer_class = RatingSerializers


@api_view(["GET"])
def get_my_ratings(request, pk):
    user = get_object_or_404(User, pk=pk)
    ratings = Rating.objects.filter(Q(client=user) | Q(artisan=user))
    serialized_data = RatingSerializers(ratings, many=True)  # Serialize the queryset
    return response.Response(serialized_data.data)

class NotificationViewSet(ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)
    
    def perform_create(self, serializer):
        serializer.save(recipient=self.request.user)
        logger.info(f"Notification created for {self.request.user.email}: {serializer.validated_data['message']}")

    @action(detail=True, methods=['patch'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        """Mark a single notification as read."""
        try:
            notification = self.get_queryset().get(pk=pk)
        except Notification.DoesNotExist:
            logger.error(f"Notification {pk} not found for user {request.user.email}")
            return response.Response(
                {"error": "Notification not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        notification.is_read = True
        notification.save()
        logger.info(f"Notification {pk} marked as read for user {request.user.email}")
        return response.Response(
            {"message": "Notification marked as read."},
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        """Mark all notifications for the user as read."""
        updated_count = self.get_queryset().filter(is_read=False).update(is_read=True)
        logger.info(f"Marked {updated_count} notifications as read for user {request.user.email}")
        return response.Response(
            {"message": f"Marked {updated_count} notifications as read."},
            status=status.HTTP_200_OK
        )