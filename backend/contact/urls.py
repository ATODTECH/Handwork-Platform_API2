from django.urls import path
from .views import ContactUsView, SubscribeView, init_session

urlpatterns = [
  path('api/', ContactUsView.as_view(), name='contact'),
  path('onload/api/', init_session, name='onload'),
  path('subscribe/api/', SubscribeView.as_view(), name='subscribe'),
]