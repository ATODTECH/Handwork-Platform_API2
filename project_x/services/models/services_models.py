from django.contrib.auth.models import models
from main.models import User
# from . import ArtisanProfile


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
    

class BookService(models.Model):
    artisan = models.ForeignKey(User, on_delete=models.SET_NULL, limit_choices_to={'role': 'ARTISAN'}, null=True)
    client = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'ARTISAN'}, related_name='users')
    service = models.ForeignKey(Services, on_delete=models.CASCADE)
    renumeration = models.DecimalField(default=0.00, max_digits=12, decimal_places=2)
    completed = models.BooleanField(default=False)
    
    def __str__(self):
        return self.completed
    

class BookingSchedule(models.Model):
    SERVICE_REQUIREMENT = (
        ("OD", "One Day"),
        ("ND", "A Number of Days")
    )
    booking_service = models.ForeignKey(BookService, on_delete=models.CASCADE)
    start_date = models.DateTimeField()
    estimated_delivery_date = models.DateField()
    

class BookingPaymentOption(models.Model):
    booking_service = models.ForeignKey(BookService, on_delete=models.CASCADE)
    escrow_payment = models.BooleanField(default=False)
    cash_payment = models.BooleanField(default=False)
    
    def __str__(self):
        return self.booking_service