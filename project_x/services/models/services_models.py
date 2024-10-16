from django.contrib.auth.models import models
from main.models import User


class Services(models.Model):
    client = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    name = models.CharField(max_length=250, unique=True)
    description = models.TextField()
    active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name
    

class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    service = models.ManyToManyField(Services, related_name="service_categories")

    def __str__(self):
        return self.name
