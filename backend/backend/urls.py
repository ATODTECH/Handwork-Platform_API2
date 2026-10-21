"""
URL configuration for prohandy backend project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/atod-tech/handworkexpert/', admin.site.urls),
    path('v2/contact/', include('contact.urls')),
]
