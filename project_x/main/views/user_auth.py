from main.models import ArtisanProfile, User
from rest_framework.viewsets import ModelViewSet
from main.serializers.auth_serializers import UserSerializer, ArtisanProfileSerializer, MyTokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import permissions

class SignUpUserView(ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer


class ArtisanProfileView(ModelViewSet):
    queryset = ArtisanProfile.objects.select_related("user").prefetch_related("service_type")
    serializer_class = ArtisanProfileSerializer


class SignInUserView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = MyTokenObtainPairSerializer