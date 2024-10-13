from rest_framework.viewsets import ModelViewSet, ViewSet
from rest_framework import response, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from . import (
    # Serializers
    ServiceSerializers, 
    ServiceBookingSerializer,
    UserSerializer,
    CategorySerializers,
    
    # Model
    Services,
    BookService,
    User,
    Category
    )


class ServiceViewSet(ModelViewSet):
    queryset = Services.objects.all()
    serializer_class = ServiceSerializers


class ServiceBookingViewSet(ViewSet):
    def get_queryset(self):
        return BookService.objects.select_related("artisan", "service", "client")
    
    def create(self, request):
        serializers = ServiceBookingSerializer(data=request.data, context={'request':request})
        if serializers.is_valid():
            serializers.save()
            return response.Response(serializers.data, status=status.HTTP_201_CREATED)
        return response.Response(serializers.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def list(self, request):
        # artisan = request.user
        try:
            queryset = self.get_queryset()
        except User.DoesNotExist:
            return NotFound("User Not Found")
        serializers = ServiceBookingSerializer(queryset, many=True)
        return response.Response(serializers.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['put', 'patch'], url_path='update', url_name='update_booking')
    def update_booking(self, request, pk=None):
        try:
            # Retrieve the booking instance by ID
            booking = self.get_queryset().get(id=pk)
        except BookService.DoesNotExist:
            return response.Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        
        # Apply the partial update with patch or full update with put
        partial = request.method == 'PATCH'
        serializer = ServiceBookingSerializer(booking, data=request.data, partial=partial, context={'request': request})
        
        if serializer.is_valid():
            serializer.save()
            return response.Response(serializer.data, status=status.HTTP_200_OK)
        return response.Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

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