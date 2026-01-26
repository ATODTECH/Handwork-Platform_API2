# setup/asgi.py (Full corrected version - this will start Daphne without errors)

import os
from django.core.asgi import get_asgi_application

# Set settings module (if not already set via env var)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'setup.settings')

# CRITICAL: Call this FIRST to load Django apps, models, and registry
django_asgi_app = get_asgi_application()

# NOW safe to import anything that touches models/auth/middleware
from channels.routing import ProtocolTypeRouter, URLRouter
from chat.middleware import CustomAuthMiddlewareStack  # Your token middleware
import chat.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": CustomAuthMiddlewareStack(
        URLRouter(
            chat.routing.websocket_urlpatterns
        )
    ),
})