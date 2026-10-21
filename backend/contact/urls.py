from django.urls import path
from .views import ContactUsView, SubscribeView, Token

urlpatterns = [
  path('api/', ContactUsView.as_view(), name='contact'),
  path('onload/token/api/', Token.as_view(), name='onload'),
  path('subscribe/api/', SubscribeView.as_view(), name='subscribe'),
]