from rest_framework.viewsets import ModelViewSet, ViewSet
from rest_framework import response, status, views
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from . import (
    # Serializers
    ServiceSerializers,
    UserSerializer,
    CategorySerializers,
    
    # Model
    Services,
    User,
    Category
    )


class ServiceViewSet(ModelViewSet):
    queryset = Services.objects.all()
    serializer_class = ServiceSerializers



# Service Search
class ServiceSearchViewSet(ViewSet):
    def list(self, request):
        search_param = request.query_params.get("search")
        
        if not search_param:
            return response.Response({"detail": "Input Search Parameter"}, status=status.HTTP_400_BAD_REQUEST)
        
        service = Services.objects.filter(name__icontains=search_param)
        
        if not service.exists():
            return response.Response({"detail": "Parameter provided doesn't match any data on record"}, status=status.HTTP_204_NO_CONTENT)
        
        results = [
            {
                "name": services.name,
                "artisans": UserSerializer(services.client, many=True).data
            }
            for services in service
        ] 
        return response.Response(results, status=status.HTTP_200_OK)
    

class CategoryViewSet(ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializers


class GetServices(views.APIView):
    def list(self, request):
        try:
            service = Services.objects.all()
        except Services.DoesNotExist:
            return response.Response("Services Does Not Exist", status=status.HTTP_404_NOT_FOUND)
        
        data = {
            "id": service.id,
            "name": service.name
        }
        return response.Response(data, status=status.HTTP_200_OK)