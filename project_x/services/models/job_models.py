from django.db import models
from . import User

class JobList(models.Model):
    client = models.ForeignKey(User, on_delete=models.DO_NOTHING, limit_choices_to={'role': 'USER'},related_name='job_owner')
    artisan = models.ForeignKey(User, on_delete=models.DO_NOTHING, limit_choices_to={'role': 'ARTISAN'}, null=True)
    title = models.CharField(max_length=350)
    description = models.TextField()
    renumeration = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateField(auto_now=True)
    isJobTaken = models.BooleanField(default=False)

    def __str__(self):
        return self.title