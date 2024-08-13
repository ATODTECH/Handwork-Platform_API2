from django.contrib.auth.models import models
from . import ArtisanProfile


class Services(models.Model):
    name = models.CharField(max_length=250, unique=True)
    description = models.TextField()
    active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name
    

class Category(models.Model):
    name = models.CharField(max_length=100)
    services = models.ManyToManyField(Services, related_name="service_category")
    
    def __str__(self):
        return self.name