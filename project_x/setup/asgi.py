import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'setup.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from chat.middleware import CustomAuthMiddlewareStack 
import chat.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": CustomAuthMiddlewareStack(
        URLRouter(
            chat.routing.websocket_urlpatterns
        )
    ),
})