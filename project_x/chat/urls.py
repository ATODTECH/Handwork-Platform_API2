from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ChatViewSet

router = DefaultRouter()
router.register(r'chat/(?P<booking_id>\d+)', ChatViewSet, basename='chat')

urlpatterns = [
    path('api/', include(router.urls)),
]