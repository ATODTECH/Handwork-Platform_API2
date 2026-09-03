from django.urls import path
from .views import ContactUsView, SubscribeView

urlpatterns = [
  path('api/', ContactUsView.as_view(), name='contact'),
  path('subscribe/api/', SubscribeView.as_view(), name='subscribe'),
]