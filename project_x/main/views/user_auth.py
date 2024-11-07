from main.models import User, Skill, BankDetails, Rating
from rest_framework.viewsets import ModelViewSet, ViewSet
from main.serializers.auth_serializers import (         UserSerializer, ArtisanSerializer, MyTokenObtainPairSerializer, SkillSerializer,
            BankDetailSerializer, RatingSerializers)
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

class SignUpUserView(ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            try:
                # Try saving the user object
                self.perform_create(serializer)
            except IntegrityError as e:
                # Catch the duplicate email IntegrityError
                if 'email' in str(e):
                    raise serializers.ValidationError({"email": "This email already exists."})
                raise serializers.ValidationError({"error": "An error occurred while saving the user."})

            headers = self.get_success_headers(serializer.data)
            return response.Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResetPassword(APIView):
    def post(self, request):
        email = request.data.get('email')
        new_password = request.data.get('new_password')

        if not email or not new_password:
            return response.Response({"error": "Email and new password are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return response.Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        # Validate password strength (example: minimum length)
        if len(new_password) < 8:
            raise ValidationError("Password must be at least 8 characters long.")
        
        if not re.search(r'^(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*\W).+$', new_password):
            raise ValidationError(
            {"error": "Passwords must include at least one special symbol, one number, one lowercase letter, and one uppercase letter."})

        user.set_password(new_password)
        user.save()
        
        return response.Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)


class SignInUserView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = MyTokenObtainPairSerializer


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
    

class RatingViewSet(ModelViewSet):
    queryset = Rating.objects.select_related("artisan", "client")
    serializer_class = RatingSerializers


@api_view(["GET"])
def get_my_ratings(request, pk):
    user = get_object_or_404(User, pk=pk)
    ratings = Rating.objects.filter(Q(client=user) | Q(artisan=user))
    serialized_data = RatingSerializers(ratings, many=True)  # Serialize the queryset
    return response.Response(serialized_data.data)