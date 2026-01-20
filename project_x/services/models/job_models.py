from django.db import models
from . import User, Services
from decimal import Decimal
from django.utils import timezone

class AutoDateTimeField(models.DateTimeField):
    def pre_save(self, model_instance, add):
        return timezone.now()

class BookArtisan(models.Model):
    STATUS = (
        ("Pending", "Pending"),
        ("Accepted", "Accepted"),
        ("Declined", "Declined"),
        ("Deposit Paid", "Deposit Paid"),
        ("In Progress", "In Progress"),  
        ("Completed", "Completed"),      
        ("Paid in Full", "Paid in Full"),
    )
    client = models.ForeignKey(User, on_delete=models.DO_NOTHING, limit_choices_to={'role': 'USER'},related_name='job_owner')
    artisan = models.ForeignKey(User, on_delete=models.DO_NOTHING, limit_choices_to={'role': 'ARTISAN'}, null=True)
    services = models.ManyToManyField(Services, related_name="booked_service")
    description = models.TextField()
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    agreed_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Final agreed price set by artisan when accepting the booking"
    )
    deposit_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Amount paid as deposit (e.g., 70%)"
    )
    deposit_paid_at = models.DateTimeField(null=True, blank=True)
    balance_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Remaining amount after deposit"
    )
    fully_paid_at = models.DateTimeField(null=True, blank=True)
    address = models.CharField(max_length=400, null=True)
    scheduled_date = models.DateField(null=True)
    scheduled_time = models.TimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = AutoDateTimeField()
    status = models.CharField(max_length=30, default="Pending")

    def __str__(self):
        return self.status
    
    DEPOSIT_PERCENTAGE = Decimal('70')

    def save(self, *args, **kwargs):
        if self.agreed_amount and self.agreed_amount > 0:
            percentage = self.DEPOSIT_PERCENTAGE / Decimal('100')
            self.deposit_amount = (self.agreed_amount * percentage).quantize(Decimal('0.01'))
            self.balance_amount = (self.agreed_amount - self.deposit_amount).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

class MaterialQuote(models.Model):
    booking = models.ForeignKey(
        BookArtisan,
        on_delete=models.CASCADE,
        related_name='material_quotes'
    )
    artisan = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'ARTISAN'},
        related_name='material_quotes'
    )
    job_title = models.CharField(max_length=255)
    material_list = models.CharField(max_length=255)  # e.g. "Plywood, Nails, Paint"
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)
    job_description = models.TextField()
    requested_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=20,
        choices=[
            ('Pending', 'Pending'),
            ('Approved', 'Approved'),
            ('Rejected', 'Rejected'),
        ],
        default='Pending'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.TextField(blank=True, null=True) # Note from admin on approval/rejection

    class Meta:
        verbose_name = "Material Quote Request"
        verbose_name_plural = "Material Quote Requests"
        ordering = ['-requested_at']

    def __str__(self):
        return f"Material Quote #{self.id} - {self.job_title} by {self.artisan.get_full_name()}"