from main.models import User, Skill, BankDetails
from rest_framework.viewsets import ModelViewSet, ViewSet
from main.serializers.auth_serializers import UserSerializer, ArtisanSerializer, MyTokenObtainPairSerializer, SkillSerializer, BankDetailSerializer
from main.permissions import IsArtisanPermission
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import permissions, response, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound


class SignUpUserView(ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer


class SignInUserView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = MyTokenObtainPairSerializer


class SignUpArtisanViewSet(ViewSet):
    def get_queryset(self):
        return User.objects.filter(role="ARTISAN")
    
    def list(self, request):
        artisans = self.get_queryset()
        serializer = ArtisanSerializer(artisans, many=True)
        return response.Response(serializer.data)
    
    def create(self, request):
        serializer = ArtisanSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return response.Response(serializer.data, status=status.HTTP_201_CREATED)
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, url_path='get_artisan', url_name='retrieve_artisan')
    def retrieve_artisan(self, request, pk=None):
        try:
            artisan = self.get_queryset().get(id=pk)
        except User.DoesNotExist:
            raise NotFound("Artisan not found.")
        
        serializer = ArtisanSerializer(artisan)
        return response.Response(serializer.data, status=status.HTTP_200_OK)


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