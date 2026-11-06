from rest_framework.viewsets import ModelViewSet, ViewSet
from rest_framework import response, status, views
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from main.permissions import IsStaffOrReadOnly
from main.serializers.auth_serializers import PublicArtisanSerializer
from . import (
    # Serializers
    ServiceSerializers,
    CategorySerializers,
    
    # Model
    Services,
    User,
    Category
    )


class ServiceViewSet(ModelViewSet):
    queryset = Services.objects.select_related("client")
    serializer_class = ServiceSerializers
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        # Staff hide services from the lists customers and new artisans choose from
        return queryset.filter(active=True) if self.action == "list" else queryset



# Service Search
class ServiceSearchViewSet(ViewSet):
    def list(self, request):
        search_param = request.query_params.get("search")
        
        if not search_param:
            return response.Response({"detail": "Input Search Parameter"}, status=status.HTTP_400_BAD_REQUEST)
        
        service = Services.objects.filter(name__icontains=search_param, active=True)

        if not service.exists():
            return response.Response({"detail": "Parameter provided doesn't match any data on record"}, status=status.HTTP_204_NO_CONTENT)

        # Anyone can search without signing in, so this only returns public profiles
        results = [
            {
                "name": services.name,
                "artisans": PublicArtisanSerializer(
                    User.objects.filter(
                        skill__services=services, verification_status="APPROVED", is_active=True
                    ).distinct(),
                    many=True,
                ).data
            }
            for services in service
        ]
        return response.Response(results, status=status.HTTP_200_OK)
    

class CategoryViewSet(ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializers
    permission_classes = [IsStaffOrReadOnly]



class GetServices(views.APIView):
    def get(self, request):
        services = Services.objects.filter(active=True)

        if not services.exists():
            return response.Response({"detail": "Services do not exist"}, status=status.HTTP_404_NOT_FOUND)

        # Manually constructing the response data
        data = [
            {
                "id": service.id,
                "name": service.name
            }
            for service in services
        ]

        return response.Response(data, status=status.HTTP_200_OK)