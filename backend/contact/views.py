from django.conf import settings
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie
from django.http import JsonResponse
# from django_ratelimit.decorators import ratelimit

# Create your views here.
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
# from rest_framework.throttling import AnonRateThrottle

from .serializers import ContactSerializer, SubscribeSerializer


# class ContactRateThrottle(AnonRateThrottle):
#   scope = "contact_us"
secret = str(settings.API_KEY)

@ensure_csrf_cookie
def init_session(req):
  """ensures csrf onload"""
  if (req.headers.get('Authorization') == secret):
    return JsonResponse({'data':"accepted", 'status': "202"})
  return JsonResponse({"data":"not accepted", "status": "401"})

class ContactUsView(APIView):
  # contact/api/

  # permission_classes = [IsAuthenticated]
  # throttle_classes = [ContactRateThrottle]
  # @ratelimit(key="ip", rate="5/m", method="POST", block=True)
  def post(self, req):
    """Stores general contact user's info"""
    if (req.headers.get('Authorization') == secret):
      print(req.data.get("phoneNumber"))
      serializer = ContactSerializer(data=req.data, partial=True)
      if not serializer.is_valid():
        return Response({'data': "error", 'status': serializer.errors}, status=status.HTTP_406_NOT_ACCEPTABLE)
      # serializer.save()
      print(serializer.validated_data)
      return Response({'data': "success", 'status': "201"}, status=status.HTTP_201_CREATED)
    return Response({'data': "error", 'status': "401"}, status=status.HTTP_401_UNAUTHORIZED)
  

class SubscribeView(APIView):
  # contact/subscribe/api/
  def post(self, req):
    """Stores general subscribing user's mail"""
    if (req.headers.get('Authorization') == secret):
      serializer = SubscribeSerializer(data=req.data)
      if not serializer.is_valid():
        return Response({'data': "error", 'status': serializer.errors}, status=status.HTTP_406_NOT_ACCEPTABLE)
      serializer.save()
      return Response({'data': "success", 'status': "201"}, status=status.HTTP_201_CREATED)
    return Response({'data': "error", 'status': "401"}, status=status.HTTP_401_UNAUTHORIZED)

