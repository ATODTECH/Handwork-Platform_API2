from django.db import models
from . import User, Services
from decimal import Decimal
from django.utils import timezone

class AutoDateTimeField(models.DateTimeField):
    def pre_save(self, model_instance, add):
        return timezone.now()

class BookArtisan(models.Model):
    # Pending -> Accepted (artisan sets the price) -> Paid (customer pays it all; we hold it)
    # -> In Progress -> Completed (artisan) -> Released (customer confirms; artisan is credited).
    # "Deposit Paid" and "Paid in Full" belong to the old 70% deposit + 30% balance flow.
    STATUS = (
        ("Pending", "Pending"),
        ("Accepted", "Accepted"),
        ("Declined", "Declined"),
        ("Paid", "Paid"),
        ("Deposit Paid", "Deposit Paid"),
        ("In Progress", "In Progress"),
        ("Completed", "Completed"),
        ("Released", "Released"),
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
    # When the artisan marked the job done; it's released automatically a while after this
    completed_at = models.DateTimeField(null=True, blank=True)
    # When the artisan's earnings were added to their wallet
    released_at = models.DateTimeField(null=True, blank=True)
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
    
class Transaction(models.Model):
    artisan = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')
    booking = models.ForeignKey(BookArtisan, on_delete=models.SET_NULL, null=True)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=12, decimal_places=2)
    vat = models.DecimalField(max_digits=12, decimal_places=2)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=20)  # 'EARNING', 'WITHDRAWAL'
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.artisan.email} - ₦{self.net_amount} ({self.transaction_type})"


class Withdrawal(models.Model):
    """
    An artisan moving money from their wallet to their bank. The amount leaves the wallet when they ask;
    staff approve it, Paystack sends it, and a failure or rejection puts it back.
    """
    STATUS = (
        ("PENDING", "Waiting for staff"),
        ("PROCESSING", "Sent to Paystack"),
        ("PAID", "Paid"),
        ("FAILED", "Failed"),
        ("REJECTED", "Rejected"),
    )
    artisan = models.ForeignKey(User, on_delete=models.PROTECT, related_name='withdrawals')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUS, default="PENDING")
    # The account it goes to, as it was when requested
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=10)
    account_name = models.CharField(max_length=350)
    recipient_code = models.CharField(max_length=50)
    # Our reference for the Paystack transfer, and Paystack's own code for it
    reference = models.CharField(max_length=50, unique=True)
    transfer_code = models.CharField(max_length=50, blank=True, default="")
    # Why it was rejected or failed
    note = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.artisan.email} - ₦{self.amount} ({self.status})"

