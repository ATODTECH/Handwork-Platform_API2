import jwt
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from channels.db import database_sync_to_async
from urllib.parse import parse_qs
from main.models import User  # Adjust to your User model import path

@database_sync_to_async
def get_user_from_token(token):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get('user_id')
        if user_id:
            return User.objects.get(id=user_id)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, User.DoesNotExist):
        pass
    return AnonymousUser()

class BearerJWTAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        # Get token from Authorization header or query string
        headers = dict(scope.get('headers', []))
        auth_header = headers.get(b'authorization', b'').decode()
        query_string = scope.get('query_string', b'').decode()
        query_params = parse_qs(query_string)

        token = None

        # Prefer header: Authorization: Bearer <token>
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        # Fallback to query string: ?token=<token>
        elif 'token' in query_params:
            token = query_params['token'][0]

        if token:
            scope['user'] = await get_user_from_token(token)
        else:
            scope['user'] = AnonymousUser()

        return await self.inner(scope, receive, send)