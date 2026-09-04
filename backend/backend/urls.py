"""
URL configuration for prohandy backend project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/atod-tech/prohandy/', admin.site.urls),
    path('contact/', include('contact.urls')),
]
