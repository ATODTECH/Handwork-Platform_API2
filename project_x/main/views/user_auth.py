import logging
import os
from rest_framework import viewsets, permissions, response, status
from rest_framework.viewsets import ModelViewSet, ViewSet
from rest_framework.views import APIView
from main.models import Notification, User, Skill, BankDetails, Rating
from main.serializers.auth_serializers import (UserSerializer, ArtisanSerializer, MyTokenObtainPairSerializer, SkillSerializer, BankDetailSerializer, RatingSerializers, NotificationSerializer)
from main.permissions import IsArtisanPermission
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import serializers
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import NotFound
from django.db import IntegrityError
from django.db.models import Count, Q
from django.core.exceptions import ValidationError
import re
from django.shortcuts import get_object_or_404
from django.core.mail import send_mail
from pathlib import Path
from rest_framework.response import Response

logger = logging.getLogger('main')

class SignUpUserView(viewsets.ModelViewSet):
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
        send_mail(
            subject='Password Reset Successful',
            message=f"Hi {user.first_name},\n\nYour password has been reset successfully.",
            from_email='your.email@gmail.com',
            recipient_list=[user.email],
            fail_silently=False,
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

class SignUpArtisanViewSet(viewsets.ModelViewSet):
    queryset = User.objects.filter(role="ARTISAN")
    serializer_class = ArtisanSerializer

class SkillViewSet(viewsets.ViewSet):
    permission_classes = [IsArtisanPermission]
    
    def get_queryset(self):
        return Skill.objects.select_related('artisan').prefetch_related('services', 'images').filter(artisan=self.request.user)
    
    def list(self, request):
        try:
            skills = self.get_queryset()
            serializer = SkillSerializer(skills, many=True)
            logger.info(f"Skills retrieved for artisan {request.user.email}")
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error retrieving skills for {request.user.email}: {str(e)}")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def retrieve(self, request, pk=None):
        try:
            skill = get_object_or_404(self.get_queryset(), pk=pk)
            serializer = SkillSerializer(skill)
            logger.info(f"Skill {pk} retrieved for artisan {request.user.email}")
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error retrieving skill {pk} for {request.user.email}: {str(e)}")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def create(self, request):
        try:
            serializer = SkillSerializer(data=request.data, context={'request': request})
            if serializer.is_valid():
                skill = serializer.save()
                Notification.objects.create(
                    recipient=request.user,
                    message=f"New skill '{skill.description}' added to your profile.",
                    notification_type='SKILL'
                )
                logger.info(f"Skill created for artisan {request.user.email}: {skill.description}")
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            logger.warning(f"Invalid skill data for artisan {request.user.email}: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error creating skill for {request.user.email}: {str(e)}")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def update(self, request, pk=None):
        try:
            skill = get_object_or_404(self.get_queryset(), pk=pk)
            serializer = SkillSerializer(skill, data=request.data, context={'request': request}, partial=True)
            if serializer.is_valid():
                skill = serializer.save()
                Notification.objects.create(
                    recipient=request.user,
                    message=f"Skill '{skill.description}' updated.",
                    notification_type='SKILL'
                )
                logger.info(f"Skill {pk} updated for artisan {request.user.email}")
                return Response(serializer.data, status=status.HTTP_200_OK)
            logger.warning(f"Invalid skill update data for artisan {request.user.email}: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error updating skill {pk} for {request.user.email}: {str(e)}")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
class BankDetailViewSet(ViewSet):
    permission_classes = [IsArtisanPermission]
    
    def get_queryset(self):
        return BankDetails.objects.select_related("artisan")
    
    def create(self, request):
        serializer = BankDetailSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            bank_detail = serializer.save()
            Notification.objects.create(
                recipient=request.user,
                message=f"Bank details for account '{bank_detail.account_number}' added.",
                notification_type='BANK_DETAILS'
            )
            logger.info(f"Bank details created for artisan {request.user.email}: {bank_detail.account_number}")
            return response.Response(serializer.data, status=status.HTTP_201_CREATED)
        logger.warning(f"Invalid bank detail data for artisan {request.user.email}: {serializer.errors}")
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def list(self, request):
        queryset = self.get_queryset()
        serializer = BankDetailSerializer(queryset, many=True)
        return response.Response(serializer.data, status=status.HTTP_200_OK)
    
    def update(self, request, pk=None):
        try:
            bank_detail = self.get_queryset().get(pk=pk)
        except BankDetails.DoesNotExist:
            logger.error(f"Bank detail {pk} not found for user {request.user.email}")
            return response.Response(
                {"detail": "Bank detail not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        serializer = BankDetailSerializer(bank_detail, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            bank_detail = serializer.save()
            Notification.objects.create(
                recipient=request.user,
                message=f"Bank details for account '{bank_detail.account_number}' updated.",
                notification_type='BANK_DETAILS'
            )
            logger.info(f"Bank detail {pk} updated for user {request.user.email}: {bank_detail.account_number}")
            return response.Response(serializer.data, status=status.HTTP_200_OK)
        logger.warning(f"Invalid bank detail update data for user {request.user.email}: {serializer.errors}")
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class RatingViewSet(viewsets.ModelViewSet):
    queryset = Rating.objects.select_related("artisan", "client")
    serializer_class = RatingSerializers

    def perform_create(self, serializer):
        rating = serializer.save()
        Notification.objects.create(
            recipient=rating.artisan,
            message=f"You received a {rating.star}-star rating from {rating.client.first_name}.",
            notification_type='RATING'
        )
        logger.info(f"Rating created: {rating.star} stars for artisan {rating.artisan.email}")

class NotificationViewSet(viewsets.ModelViewSet):
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

class LogsView(APIView):
    permission_classes = []

    def get(self, request):
        """Retrieve contents of the log file."""
        log_file_path = os.path.join(Path(__file__).resolve().parent.parent, '../logs', 'user_artisan.log')
        try:
            with open(log_file_path, 'r') as log_file:
                log_content = log_file.read()
            return response.Response(
                {"logs": log_content},
                status=status.HTTP_200_OK
            )
        except FileNotFoundError:
            logger.error(f"Log file not found at {log_file_path}")
            return response.Response(
                {"error": "Log file not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error reading log file: {str(e)}")
            return response.Response(
                {"error": f"Error reading log file: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

@api_view(["GET"])
def get_my_ratings(request, pk):
    user = get_object_or_404(User, pk=pk)
    ratings = Rating.objects.filter(Q(client=user) | Q(artisan=user))
    serialized_data = RatingSerializers(ratings, many=True)
    return response.Response(serialized_data.data)