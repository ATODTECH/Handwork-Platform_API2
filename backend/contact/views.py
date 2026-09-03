from django.shortcuts import get_object_or_404
# from django_ratelimit.decorators import ratelimit
from .models import Contact, Subscribe

# Create your views here.
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
# from rest_framework.throttling import AnonRateThrottle

from .serializers import ContactSerializer, SubscribeSerializer


# class ContactRateThrottle(AnonRateThrottle):
#   scope = "contact_us"

class ContactUsView(APIView):
  permission_classes = [IsAuthenticated]
  # throttle_classes = [ContactRateThrottle]
  # contact/api/
  # @ratelimit(key="ip", rate="5/m", method="POST", block=True)
  def post(self, req):
    """Stores general contact user's info"""
    serializer = ContactSerializer(data=req.data)
    if not serializer.is_valid():
      return Response({'status': "error", 'errors': serializer.errors}, status=status.HTTP_406_NOT_ACCEPTABLE)
    serializer.save()
    return Response({'status': "success"}, status=status.HTTP_201_CREATED)

class SubscribeView(APIView):
  permission_classes = [IsAuthenticated]
  # contact/subscribe/api/
  def post(self, req):
    """Stores general subscribing user's mail"""
    serializer = SubscribeSerializer(data=req.data)
    if not serializer.is_valid():
      return Response({'status': "error", 'errors': serializer.errors}, status=status.HTTP_406_NOT_ACCEPTABLE)
    serializer.save()
    return Response({'status': "success"}, status=status.HTTP_201_CREATED)

