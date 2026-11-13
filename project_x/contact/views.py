from django.conf import settings

from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from main.emails import send_email

from .sessions import createSession, verifySession
from .serializers import ContactSerializer, SubscribeSerializer


secret = str(settings.API_KEY)


def send_contact_email(contact):
  send_email(
    settings.CONTACT_FORM_EMAIL,
    f"Website message from {contact.name}",
    f"Name: {contact.name}\nEmail: {contact.email}\nPhone: {contact.phoneNumber}\n\n{contact.message}\n\n"
    "Sent from the contact form on the Handwork Expert website. Reply to this email to answer them.",
    reply_to=contact.email,
  )

class Token(APIView):
  """get route `onload/token/api`"""
  def get(self, req):
    """Token/Session created onload a webpage"""
    sessionToken: str = req.session.get('_userCookie') # retrieveToken
    if (req.headers.get('Authorization') == secret):
      verifyToken = verifySession(sessionToken) # objectUserData

      if verifyToken.get('isAuth'):
        return Response({'data':"success"}, status=status.HTTP_200_OK)
      
      createToken = createSession({'_userHID': 0}) # token
      req.session['_userCookie'] = createToken # createSession
      return Response({'data':"success"}, status=status.HTTP_200_OK)
    return Response({'data':"error"}, status=status.HTTP_403_FORBIDDEN)


class PublicFormView(APIView):
  """
  The website's public forms. Anyone may send them, so they're limited per visitor (the `contact` throttle
  rate) instead of the old shared key, which the browser had to carry and so couldn't be kept secret.
  """
  authentication_classes = []
  permission_classes = [AllowAny]
  throttle_classes = [ScopedRateThrottle]
  throttle_scope = "contact"


class ContactUsView(PublicFormView):
  """post route `v2/contact/api/`"""
  def post(self, req):
    """Stores the message and emails it to the website's inbox"""
    serializer = ContactSerializer(data=req.data)
    if not serializer.is_valid():
      return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    contact = serializer.save()
    # Emailed to the website's inbox; replying answers the visitor directly
    send_contact_email(contact)
    return Response({'data': "success", 'status': "201"}, status=status.HTTP_201_CREATED)


class SubscribeView(PublicFormView):
  """post route `v2/contact/subscribe/api/`"""
  def post(self, req):
    """Stores a newsletter sign-up"""
    serializer = SubscribeSerializer(data=req.data)
    if not serializer.is_valid():
      return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response({'data': "success", 'status': "201"}, status=status.HTTP_201_CREATED)
