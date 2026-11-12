import logging
import random
import string
from rest_framework import viewsets, permissions, response, status
from rest_framework.viewsets import ViewSet
from rest_framework.views import APIView
from main.models import Notification, User, Skill, BankDetails, Rating
from main.serializers.auth_serializers import (UserSerializer, ArtisanSerializer, PublicArtisanSerializer, MyTokenObtainPairSerializer, SkillSerializer, BankDetailSerializer, RatingSerializers, NotificationSerializer,
    REVIEW_EDIT_DAYS, ReviewReplySerializer, visible_reviews)
from main.permissions import IsArtisanPermission, IsRatingAuthor, IsSelfOrStaff, IsUserPermission
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import serializers
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from datetime import timedelta
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.utils import timezone
from django.db.models import Q
from django.core.cache import cache
import re
from django.shortcuts import get_object_or_404
from django.http import Http404
from chat.messaging import CLOSED_STATUSES
from main.emails import greeting, send_email
from services.models.job_models import BookArtisan

logger = logging.getLogger('main')


class SignUpUserView(viewsets.ModelViewSet):
    """Customer sign-up, plus each account's own profile (an artisan may also read/edit theirs here)."""
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def get_permissions(self):
        if self.action == "create":
            return [AllowAny()]
        if self.action == "list":
            return [IsAdminUser()]
        return [IsSelfOrStaff()]

    def perform_create(self, serializer):
        user = serializer.save(role="USER")
        # Signed up through a friend's invite link or code
        from referrals.rewards import link_referral
        link_referral(user, self.request.data.get("referral_code"))

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
                send_email(
                    user.email,
                    'Welcome to Handwork Expert!',
                    f"{greeting(user)}\n\nYour account has been created successfully.\n\nWelcome to Handwork Expert!",
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


class ForgotPasswordRequestOTP(APIView):
    permission_classes = []

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return response.Response(
                {"error": "Email is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return response.Response(
                {"message": "If this email exists, an OTP has been sent."},
                status=status.HTTP_200_OK
            )

        otp = ''.join(random.choices(string.digits, k=6))
        cache_key = f"password_reset_otp_{email}"
        cache.set(cache_key, otp, timeout=600)

        send_email(
            user.email,
            'Your password reset code - Handwork Expert',
            f"{greeting(user)}\n\nYour code to reset your password is: {otp}\n\nIt expires in 10 minutes.\n\nIf you didn't ask to reset your password, you can ignore this email.",
        )

        return response.Response(
            {"message": "If this email exists, an OTP has been sent."},
            status=status.HTTP_200_OK
        )


class ForgotPasswordVerifyOTP(APIView):
    permission_classes = []

    def post(self, request):
        email = request.data.get('email')
        otp = request.data.get('otp')

        if not email or not otp:
            return response.Response(
                {"error": "Email and OTP are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        cache_key = f"password_reset_otp_{email}"
        cached_otp = cache.get(cache_key)

        if not cached_otp:
            return response.Response(
                {"error": "OTP has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if cached_otp != otp:
            return response.Response(
                {"error": "Invalid OTP."},
                status=status.HTTP_400_BAD_REQUEST
            )

        cache.delete(cache_key)
        verified_key = f"password_reset_verified_{email}"
        cache.set(verified_key, True, timeout=600)

        logger.info(f"OTP verified for {email}")
        return response.Response(
            {"message": "OTP verified. You can now reset your password."},
            status=status.HTTP_200_OK
        )


class ResetPassword(APIView):
    permission_classes = []

    def post(self, request):
        email = request.data.get('email')
        new_password = request.data.get('new_password')

        if not email or not new_password:
            logger.warning("Password reset failed: Missing email or password")
            return response.Response(
                {"error": "Email and new password are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        verified_key = f"password_reset_verified_{email}"
        if not cache.get(verified_key):
            return response.Response(
                {"error": "OTP verification required before resetting password."},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.error(f"Password reset failed: User not found for email {email}")
            return response.Response(
                {"error": "User not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        if len(new_password) < 8:
            return response.Response(
                {"error": "Password must be at least 8 characters long."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not re.search(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', new_password):
            return response.Response(
                {"error": "Password must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()
        cache.delete(verified_key)

        Notification.objects.create(
            recipient=user,
            message="Your password has been successfully reset.",
            notification_type='PASSWORD_RESET'
        )

        send_email(
            user.email,
            'Password Reset Successful - Handwork Expert',
            f"{greeting(user)}\n\nYour password has been reset successfully.\n\nIf you did not do this, please contact support immediately.",
        )

        logger.info(f"Password reset successful for {email}")
        return response.Response(
            {"message": "Password changed successfully."},
            status=status.HTTP_200_OK
        )


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
    """
    Artisan sign-up and profiles. Anyone may browse artisans, but only the public profile;
    the full record (contact details, ID numbers, documents) is for the artisan themselves.
    """
    queryset = User.objects.filter(role="ARTISAN")

    def get_queryset(self):
        queryset = super().get_queryset()
        # Customers only ever find artisans staff have approved
        return queryset.filter(verification_status="APPROVED", is_active=True) if self.action == "list" else queryset

    def get_permissions(self):
        if self.action in ("create", "list", "retrieve"):
            return [AllowAny()]
        return [IsSelfOrStaff()]

    def get_serializer_class(self):
        return PublicArtisanSerializer if self.action == "list" else ArtisanSerializer

    def perform_create(self, serializer):
        user = serializer.save(role="ARTISAN")
        from referrals.rewards import link_referral
        link_referral(user, self.request.data.get("referral_code"))

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        is_owner = request.user.is_authenticated and (request.user == instance or request.user.is_staff)
        if not is_owner and (instance.verification_status != "APPROVED" or not instance.is_active):
            raise Http404
        serializer_class = ArtisanSerializer if is_owner else PublicArtisanSerializer
        return response.Response(serializer_class(instance, context=self.get_serializer_context()).data)


class SkillViewSet(ViewSet):
    permission_classes = [IsArtisanPermission]

    def get_queryset(self):
        return Skill.objects.select_related("artisan")

    def create(self, request):
        serializer = SkillSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            skill = serializer.save()
            Notification.objects.create(
                recipient=request.user,
                message=f"New skill '{skill.description}' added to your profile.",
                notification_type='SKILL'
            )
            logger.info(f"Skill created for artisan {request.user.email}: {skill.description}")
            return response.Response(serializer.data, status=status.HTTP_201_CREATED)
        logger.warning(f"Invalid skill data for artisan {request.user.email}: {serializer.errors}")
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def list(self, request):
        skill = self.get_queryset()
        serializer = SkillSerializer(skill, many=True)
        return response.Response(serializer.data, status=status.HTTP_200_OK)

    def retrieve(self, request, pk=None):
        skill = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = SkillSerializer(skill)
        return response.Response(serializer.data, status=status.HTTP_200_OK)

    def partial_update(self, request, pk=None):
        # An artisan only ever changes their own skill
        skill = get_object_or_404(self.get_queryset(), pk=pk, artisan=request.user)
        serializer = SkillSerializer(skill, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(f"Skill {pk} updated by artisan {request.user.email}")
        return response.Response(serializer.data, status=status.HTTP_200_OK)


class BankDetailViewSet(ViewSet):
    permission_classes = [IsArtisanPermission]

    def get_queryset(self):
        # An artisan only ever sees or changes their own bank details
        return BankDetails.objects.select_related("artisan").filter(artisan=self.request.user)

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
    """
    Reviews. Anyone may read them (?artisan=<id> for one artisan's). A customer reviews a job once it's been
    released (`booking`), can change it for REVIEW_EDIT_DAYS and until the artisan replies, and can't delete it.
    The artisan replies with POST /artisan_rating/{id}/reply.
    """
    serializer_class = RatingSerializers
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        queryset = visible_reviews().select_related("artisan", "client", "booking").prefetch_related("booking__services")
        artisan = self.request.query_params.get("artisan")
        if artisan:
            try:
                queryset = queryset.filter(artisan_id=artisan)
            except (ValueError, DjangoValidationError):
                return Rating.objects.none()
        return queryset

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        if self.action == "create":
            return [IsUserPermission()]
        if self.action == "reply":
            return [IsAuthenticated()]
        return [IsRatingAuthor()]

    def perform_create(self, serializer):
        booking = serializer.validated_data["booking"]
        rating = serializer.save(client=self.request.user, artisan=booking.artisan)
        Notification.objects.create(
            recipient=rating.artisan,
            message=f"{rating.client.first_name} left you a {rating.star}-star review. You can reply to it.",
            notification_type='RATING'
        )
        logger.info(f"Review {rating.id}: {rating.star} stars for artisan {rating.artisan.email} (booking {booking.id})")

    def partial_update(self, request, *args, **kwargs):
        rating = self.get_object()
        if rating.reply:
            return response.Response({"detail": "The artisan has replied, so this review can't be changed."},
                                     status=status.HTTP_400_BAD_REQUEST)
        if rating.created_at < timezone.now() - timedelta(days=REVIEW_EDIT_DAYS):
            return response.Response({"detail": f"A review can only be changed within {REVIEW_EDIT_DAYS} days."},
                                     status=status.HTTP_400_BAD_REQUEST)
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        """The reviewed artisan answers the review (or changes their answer)"""
        rating = self.get_object()
        if rating.artisan_id != request.user.pk:
            return response.Response({"detail": "Only the artisan who was reviewed can reply."},
                                     status=status.HTTP_403_FORBIDDEN)
        serializer = ReviewReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        first_reply = not rating.reply
        rating.reply = serializer.validated_data["reply"]
        rating.replied_at = timezone.now()
        rating.save(update_fields=["reply", "replied_at", "updated_at"])
        if first_reply:
            Notification.objects.create(
                recipient=rating.client,
                message=f"{rating.artisan.first_name} replied to your review.",
                notification_type='RATING'
            )
        return response.Response(RatingSerializers(rating, context={"request": request}).data)


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
        updated_count = self.get_queryset().filter(is_read=False).update(is_read=True)
        logger.info(f"Marked {updated_count} notifications as read for user {request.user.email}")
        return response.Response(
            {"message": f"Marked {updated_count} notifications as read."},
            status=status.HTTP_200_OK
        )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_account(request):
    """Deactivates the account, unless a booking is still open or the artisan still has money with us"""
    user = request.user
    open_bookings = BookArtisan.objects.filter(Q(client=user) | Q(artisan=user)).exclude(status__in=CLOSED_STATUSES)
    if open_bookings.exists():
        return response.Response(
            {"detail": "You still have open bookings. Finish or cancel them before deleting your account."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if user.wallet_balance > 0 or user.withdrawals.filter(status__in=("PENDING", "PROCESSING")).exists():
        return response.Response(
            {"detail": "You still have money in your wallet. Withdraw it before deleting your account."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    user.is_active = False
    user.save(update_fields=["is_active"])
    logger.info(f"Account deactivated for user {user.email}")
    return response.Response(
        {"message": "Account successfully deactivated."},
        status=status.HTTP_200_OK
    )

@api_view(["GET"])
def get_my_ratings(request, pk):
    user = get_object_or_404(User, pk=pk)
    ratings = visible_reviews().filter(Q(client=user) | Q(artisan=user)).select_related('client', 'artisan', 'booking')
    serialized_data = RatingSerializers(ratings, many=True)
    return response.Response(serialized_data.data)



# import logging
# import os
# from rest_framework import viewsets, permissions, response, status
# from rest_framework.viewsets import ModelViewSet, ViewSet
# from rest_framework.views import APIView
# from main.models import Notification, User, Skill, BankDetails, Rating
# from main.serializers.auth_serializers import (UserSerializer, ArtisanSerializer, PublicArtisanSerializer, MyTokenObtainPairSerializer, SkillSerializer, BankDetailSerializer, RatingSerializers, NotificationSerializer)
# from main.permissions import IsArtisanPermission, IsRatingAuthor, IsSelfOrStaff, IsUserPermission
# from rest_framework_simplejwt.views import TokenObtainPairView
# from rest_framework import serializers
# from rest_framework.decorators import action, api_view
# from rest_framework.exceptions import NotFound
# from django.db import IntegrityError
# from django.db.models import Count, Q
# from django.core.exceptions import ValidationError
# import re
# from django.shortcuts import get_object_or_404
# from django.core.mail import send_mail
# from pathlib import Path

# logger = logging.getLogger('main')

# class SignUpUserView(viewsets.ModelViewSet):
#     queryset = User.objects.all()
#     serializer_class = UserSerializer
    
#     def create(self, request, *args, **kwargs):
#         serializer = self.get_serializer(data=request.data)
#         if serializer.is_valid():
#             try:
#                 self.perform_create(serializer)
#                 user = User.objects.get(email=serializer.data['email'])
#                 Notification.objects.create(
#                     recipient=user,
#                     message=f"Welcome, {user.first_name}! Your account has been created.",
#                     notification_type='SIGNUP'
#                 )
#                 send_mail(
#                     subject='Welcome to User Artisan!',
#                     message=f"Hi {user.first_name},\n\nYour account has been created successfully.",
#                     from_email='your.email@gmail.com',
#                     recipient_list=[user.email],
#                     fail_silently=False,
#                 )
#                 logger.info(f"User created: {serializer.data['email']} (ID: {serializer.data['id']})")
#             except IntegrityError as e:
#                 logger.error(f"Failed to create user: {str(e)}")
#                 if 'email' in str(e):
#                     raise serializers.ValidationError({"email": "This email already exists."})
#                 raise serializers.ValidationError({"error": "An error occurred while saving the user."})
#             headers = self.get_success_headers(serializer.data)
#             return response.Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
#         logger.warning(f"Invalid user signup data: {serializer.errors}")
#         return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# class ResetPassword(APIView):
#     def post(self, request):
#         email = request.data.get('email')
#         new_password = request.data.get('new_password')
#         if not email or not new_password:
#             logger.warning("Password reset failed: Missing email or password")
#             return response.Response({"error": "Email and new password are required."}, status=status.HTTP_400_BAD_REQUEST)
#         try:
#             user = User.objects.get(email=email)
#         except User.DoesNotExist:
#             logger.error(f"Password reset failed: User not found for email {email}")
#             return response.Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
#         if len(new_password) < 8:
#             logger.warning(f"Password reset failed for {email}: Password too short")
#             raise ValidationError("Password must be at least 8 characters long.")
#         if not re.search(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', new_password):
#             logger.warning(f"Password reset failed for {email}: Invalid password format")
#             raise ValidationError({"error": "Passwords must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."})
#         user.set_password(new_password)
#         user.save()
#         Notification.objects.create(
#             recipient=user,
#             message=f"Your password has been successfully reset.",
#             notification_type='PASSWORD_RESET'
#         )
#         send_mail(
#             subject='Password Reset Successful',
#             message=f"Hi {user.first_name},\n\nYour password has been reset successfully.",
#             from_email='your.email@gmail.com',
#             recipient_list=[user.email],
#             fail_silently=False,
#         )
#         logger.info(f"Password reset successful for {email}")
#         return response.Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)

# class SignInUserView(TokenObtainPairView):
#     permission_classes = (permissions.AllowAny,)
#     serializer_class = MyTokenObtainPairSerializer

#     def post(self, request, *args, **kwargs):
#         response = super().post(request, *args, **kwargs)
#         if response.status_code == 200:
#             logger.info(f"User signed in: {request.data.get('email')}")
#         else:
#             logger.warning(f"Sign-in failed: {response.data}")
#         return response

# class SignUpArtisanViewSet(viewsets.ModelViewSet):
#     queryset = User.objects.filter(role="ARTISAN")
#     serializer_class = ArtisanSerializer

# class SkillViewSet(ViewSet):
#     permission_classes = [IsArtisanPermission]
    
#     def get_queryset(self):
#         return Skill.objects.select_related("artisan")
    
#     def create(self, request):
#         serializer = SkillSerializer(data=request.data, context={'request': request})
#         if serializer.is_valid():
#             skill = serializer.save()
#             Notification.objects.create(
#                 recipient=request.user,
#                 message=f"New skill '{skill.description}' added to your profile.",
#                 notification_type='SKILL'
#             )
#             logger.info(f"Skill created for artisan {request.user.email}: {skill.description}")
#             return response.Response(serializer.data, status=status.HTTP_201_CREATED)
#         logger.warning(f"Invalid skill data for artisan {request.user.email}: {serializer.errors}")
#         return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
#     def list(self, request):
#         skill = self.get_queryset()
#         serializer = SkillSerializer(skill, many=True)
#         return response.Response(serializer.data, status=status.HTTP_200_OK)
    
#     def retrieve(self, request, pk=None):
#         skill = get_object_or_404(self.get_queryset(), pk=pk)
#         serializer = SkillSerializer(skill)
#         return response.Response(serializer.data, status=status.HTTP_200_OK)

# class BankDetailViewSet(ViewSet):
#     permission_classes = [IsArtisanPermission]
    
#     def get_queryset(self):
#         return BankDetails.objects.select_related("artisan")
    
#     def create(self, request):
#         serializer = BankDetailSerializer(data=request.data, context={'request': request})
#         if serializer.is_valid():
#             bank_detail = serializer.save()
#             Notification.objects.create(
#                 recipient=request.user,
#                 message=f"Bank details for account '{bank_detail.account_number}' added.",
#                 notification_type='BANK_DETAILS'
#             )
#             logger.info(f"Bank details created for artisan {request.user.email}: {bank_detail.account_number}")
#             return response.Response(serializer.data, status=status.HTTP_201_CREATED)
#         logger.warning(f"Invalid bank detail data for artisan {request.user.email}: {serializer.errors}")
#         return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#     def list(self, request):
#         queryset = self.get_queryset()
#         serializer = BankDetailSerializer(queryset, many=True)
#         return response.Response(serializer.data, status=status.HTTP_200_OK)
    
#     def update(self, request, pk=None):
#         try:
#             bank_detail = self.get_queryset().get(pk=pk)
#         except BankDetails.DoesNotExist:
#             logger.error(f"Bank detail {pk} not found for user {request.user.email}")
#             return response.Response(
#                 {"detail": "Bank detail not found."},
#                 status=status.HTTP_404_NOT_FOUND,
#             )
        
#         serializer = BankDetailSerializer(bank_detail, data=request.data, partial=True, context={'request': request})
#         if serializer.is_valid():
#             bank_detail = serializer.save()
#             Notification.objects.create(
#                 recipient=request.user,
#                 message=f"Bank details for account '{bank_detail.account_number}' updated.",
#                 notification_type='BANK_DETAILS'
#             )
#             logger.info(f"Bank detail {pk} updated for user {request.user.email}: {bank_detail.account_number}")
#             return response.Response(serializer.data, status=status.HTTP_200_OK)
#         logger.warning(f"Invalid bank detail update data for user {request.user.email}: {serializer.errors}")
#         return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# class RatingViewSet(viewsets.ModelViewSet):
#     queryset = Rating.objects.select_related("artisan", "client")
#     serializer_class = RatingSerializers

#     def perform_create(self, serializer):
#         rating = serializer.save()
#         Notification.objects.create(
#             recipient=rating.artisan,
#             message=f"You received a {rating.star}-star rating from {rating.client.first_name}.",
#             notification_type='RATING'
#         )
#         logger.info(f"Rating created: {rating.star} stars for artisan {rating.artisan.email}")

# class NotificationViewSet(viewsets.ModelViewSet):
#     serializer_class = NotificationSerializer
#     permission_classes = [permissions.IsAuthenticated]

#     def get_queryset(self):
#         return Notification.objects.filter(recipient=self.request.user)
    
#     def perform_create(self, serializer):
#         serializer.save(recipient=self.request.user)
#         logger.info(f"Notification created for {self.request.user.email}: {serializer.validated_data['message']}")

#     @action(detail=True, methods=['patch'], url_path='mark-read')
#     def mark_read(self, request, pk=None):
#         """Mark a single notification as read."""
#         try:
#             notification = self.get_queryset().get(pk=pk)
#         except Notification.DoesNotExist:
#             logger.error(f"Notification {pk} not found for user {request.user.email}")
#             return response.Response(
#                 {"error": "Notification not found."},
#                 status=status.HTTP_404_NOT_FOUND
#             )
        
#         notification.is_read = True
#         notification.save()
#         logger.info(f"Notification {pk} marked as read for user {request.user.email}")
#         return response.Response(
#             {"message": "Notification marked as read."},
#             status=status.HTTP_200_OK
#         )

#     @action(detail=False, methods=['post'], url_path='mark-all-read')
#     def mark_all_read(self, request):
#         """Mark all notifications for the user as read."""
#         updated_count = self.get_queryset().filter(is_read=False).update(is_read=True)
#         logger.info(f"Marked {updated_count} notifications as read for user {request.user.email}")
#         return response.Response(
#             {"message": f"Marked {updated_count} notifications as read."},
#             status=status.HTTP_200_OK
#         )

# class LogsView(APIView):
#     permission_classes = []

#     def get(self, request):
#         """Retrieve contents of the log file."""
#         log_file_path = os.path.join(Path(__file__).resolve().parent.parent, '../logs', 'user_artisan.log')
#         try:
#             with open(log_file_path, 'r') as log_file:
#                 log_content = log_file.read()
#             return response.Response(
#                 {"logs": log_content},
#                 status=status.HTTP_200_OK
#             )
#         except FileNotFoundError:
#             logger.error(f"Log file not found at {log_file_path}")
#             return response.Response(
#                 {"error": "Log file not found."},
#                 status=status.HTTP_404_NOT_FOUND
#             )
#         except Exception as e:
#             logger.error(f"Error reading log file: {str(e)}")
#             return response.Response(
#                 {"error": f"Error reading log file: {str(e)}"},
#                 status=status.HTTP_500_INTERNAL_SERVER_ERROR
#             )

# @api_view(["GET"])
# def get_my_ratings(request, pk):
#     user = get_object_or_404(User, pk=pk)
#     ratings = visible_reviews().filter(Q(client=user) | Q(artisan=user)).select_related('client', 'artisan', 'booking')
#     serialized_data = RatingSerializers(ratings, many=True)
#     return response.Response(serialized_data.data)