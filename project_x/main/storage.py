from django.conf import settings
from storages.backends.s3 import S3Storage


class SpacesStorage(S3Storage):
    """
    DigitalOcean Spaces (S3-compatible). Private by default: objects are only reachable
    through short-lived signed URLs, which suits identity documents.
    """

    def __init__(self, **kwargs):
        options = {
            'access_key': settings.SPACES_KEY,
            'secret_key': settings.SPACES_SECRET,
            'bucket_name': settings.SPACES_BUCKET,
            'region_name': settings.SPACES_REGION,
            'endpoint_url': settings.SPACES_ENDPOINT,
            'location': settings.SPACES_PREFIX,
            'default_acl': 'private',
            'querystring_auth': True,
            'querystring_expire': settings.PRIVATE_MEDIA_URL_EXPIRE,
            'file_overwrite': False,
            'signature_version': 's3v4',
            'addressing_style': 'virtual',
            'object_parameters': {'CacheControl': 'private, no-store'},
        }
        options.update(kwargs)
        super().__init__(**options)


class PublicSpacesStorage(SpacesStorage):
    """Public-read objects with plain, cacheable URLs: profile pictures and work photos."""

    def __init__(self, **kwargs):
        options = {
            'default_acl': 'public-read',
            'querystring_auth': False,
            'object_parameters': {'CacheControl': 'public, max-age=31536000'},
        }
        options.update(kwargs)
        super().__init__(**options)


# Callables, so models and migrations don't build a storage (or read settings) at import time
def private_media_storage():
    return SpacesStorage()


def public_media_storage():
    return PublicSpacesStorage()
