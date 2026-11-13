from django.conf import settings

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


class ContactUsView(APIView):
  """post route `v2/contact/api/`"""
  def post(self, req):
    """Stores general contact user's info"""
    if (req.headers.get('Authorization') == secret):
      serializer = ContactSerializer(data=req.data, partial=True)
      if not serializer.is_valid():
        return Response({'data': "error", 'status': serializer.errors}, status=status.HTTP_406_NOT_ACCEPTABLE)
      contact = serializer.save()
      # Emailed to the website's inbox; replying answers the visitor directly
      send_contact_email(contact)
      return Response({'data': "success", 'status': "201"}, status=status.HTTP_201_CREATED)
    return Response({'data': "error", 'status': "401"}, status=status.HTTP_401_UNAUTHORIZED)
  

class SubscribeView(APIView):
  """post route `v2/contact/subscribe/api/`"""
  def post(self, req):
    """Stores general subscribing user's mail"""
    if (req.headers.get('Authorization') == secret):
      serializer = SubscribeSerializer(data=req.data)
      if not serializer.is_valid():
        return Response({'data': "error", 'status': serializer.errors}, status=status.HTTP_406_NOT_ACCEPTABLE)
      serializer.save()
      return Response({'data': "success", 'status': "201"}, status=status.HTTP_201_CREATED)
    return Response({'data': "error", 'status': "401"}, status=status.HTTP_401_UNAUTHORIZED)
