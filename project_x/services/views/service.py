from rest_framework.viewsets import ModelViewSet
from . import (
    ServiceSerializers, 
    ServiceCategorySerializers,
    ArtisanProfile, 
    # ArtisanService
    Services,
    Category
    )


class ServiceTypeViewSet(ModelViewSet):
    queryset = Services.objects.all()
    serializer_class = ServiceSerializers
    
    
class CategoryViewSet(ModelViewSet):
    queryset = Category.objects.prefetch_related("services")
    serializer_class = ServiceCategorySerializers