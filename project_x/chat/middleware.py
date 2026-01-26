# chat/middleware.py
import jwt
from jwt.exceptions import PyJWTError
from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from channels.db import database_sync_to_async
from channels.auth import AuthMiddlewareStack
from urllib.parse import parse_qs
from main.models import User 

@database_sync_to_async
def get_user_from_token(token):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get('user_id')
        if user_id:
            return User.objects.get(id=user_id)
    except (PyJWTError, User.DoesNotExist):
        pass
    return AnonymousUser()

class TokenAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get('headers', []))
        auth_header = headers.get(b'authorization', b'').decode()
        query_string = scope.get('query_string', b'').decode()
        query_params = parse_qs(query_string)
        token = None

        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        elif query_params.get('token'):
            token = query_params['token'][0]

        if token:
            scope['user'] = await get_user_from_token(token)
        else:
            scope['user'] = AnonymousUser()

        return await self.inner(scope, receive, send)

def CustomAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(AuthMiddlewareStack(inner))