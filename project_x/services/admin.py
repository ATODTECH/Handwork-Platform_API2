from django.contrib import admin
from services.models.services_models import Services, Category
from services.models.job_models import BookArtisan


# Register your models here.
admin.site.register(Services)
admin.site.register(Category)
admin.site.register(BookArtisan)