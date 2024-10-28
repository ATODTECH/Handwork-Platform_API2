from django.db import models
from . import User, Services
from django.utils import timezone

class AutoDateTimeField(models.DateTimeField):
    def pre_save(self, model_instance, add):
        return timezone.now()

class BookArtisan(models.Model):
    STATUS = (
        ("Pending", "Pending"),
        ("Accepted", "Accepted"),
        ("Declined", "Declined")
    )
    client = models.ForeignKey(User, on_delete=models.DO_NOTHING, limit_choices_to={'role': 'USER'},related_name='job_owner')
    artisan = models.ForeignKey(User, on_delete=models.DO_NOTHING, limit_choices_to={'role': 'ARTISAN'}, null=True)
    services = models.ManyToManyField(Services, related_name="booked_service")
    description = models.TextField()
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    address = models.CharField(max_length=400, null=True)
    scheduled_date = models.DateField(null=True)
    scheduled_time = models.TimeField(null=True)
    created_at = models.DateField(default=timezone.now)
    updated_at = AutoDateTimeField(default=timezone.now)
    status = models.CharField(max_length=7, default="Pending")

    def __str__(self):
        return self.status