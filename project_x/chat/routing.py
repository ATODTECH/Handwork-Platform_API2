from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/chat/(?P<booking_id>\d+)/$', consumers.ChatConsumer.as_asgi()),
    re_path(r'ws/requests/(?P<request_id>\d+)/(?P<provider_id>[0-9a-fA-F-]{32,36})/$', consumers.RequestChatConsumer.as_asgi()),
]