from django.contrib import admin
from services.models.services_models import Services, Category

# Register your models here.
admin.site.register(Services)
admin.site.register(Category)