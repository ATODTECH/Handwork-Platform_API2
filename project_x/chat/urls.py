from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ChatViewSet, conversations

router = DefaultRouter()
router.register(r'chat/(?P<booking_id>\d+)', ChatViewSet, basename='chat')

# This router keeps Django's trailing slashes: /api/chat/<booking_id>/, .../send/, .../read/
urlpatterns = [
    path('api/conversations', conversations, name='conversations'),
    path('api/', include(router.urls)),
]