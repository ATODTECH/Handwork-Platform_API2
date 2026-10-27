from django.contrib.auth.models import AbstractUser, AbstractBaseUser, BaseUserManager
import string, random
from django.db import models
from django.utils import timezone, translation
from uuid import uuid4
from datetime import timedelta
from decimal import Decimal
from main.storage import private_media_storage, public_media_storage

# from services.models.services_models import Services

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
    # Create a standard user
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        # Create a superuser
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    
    ROLE = (
        ("USER", "USER"),
        ("ARTISAN", "ARTISAN")
    )
    
    DOCUMENT_TYPE = (
        ("DL", "Driver's License"),
        ("PSP", "Passport"),
        ("NIN", "NIN Slip/Card")
    )
    
    GENDER_CHOICES = (
        ("MALE", "MALE"),
        ("FEMALE", "FEMALE")
    )
    
    # User Information
    id = models.UUIDField(default=uuid4, primary_key=True)
    first_name = models.CharField(max_length=250)
    last_name = models.CharField(max_length=250)
    email = models.EmailField(max_length=254, unique=True)
    password = models.CharField(max_length=128)
    contact = models.CharField(max_length=15, unique=True)
    profile_image = models.ImageField(
        translation.gettext_lazy('profile picture'),
        upload_to='profile_images/',
        null=True,
        blank=True,
        storage=public_media_storage
    )
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES, null=True)
    address = models.TextField(null=True)
    # Artisan Information
    state = models.CharField(max_length=40, null=True)
    city = models.CharField(max_length=40, null=True)
    latitude = models.CharField(max_length=10, null=True)
    longitude = models.CharField(max_length=10, null=True)

    country = models.CharField(max_length=80, null=True)
    nin = models.CharField(max_length=11, null=True)
    bvn = models.CharField(max_length=11, null=True)
    document = models.ImageField(
        translation.gettext_lazy('onboarding_docs'),
        upload_to='onboarding_docs/',
        null=True,
        blank=True,
        # ID documents: private, served through signed URLs only
        storage=private_media_storage
    )
    document_type = models.CharField(choices=DOCUMENT_TYPE, max_length=16, null=True, blank=True)
    role = models.CharField(max_length=7, choices=ROLE, default="USER")
    onboardingComplete = models.BooleanField(default=False)
    wallet_balance = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Withdrawable balance after deductions"
    )

    isAvailable = models.BooleanField(default=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = CustomUserManager()
    
    def save(self, *args, **kwargs):
        if not self.username:
            random_username = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
            self.username=random_username
        return super(User, self).save(*args, **kwargs)


class Skill(models.Model):
    artisan = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role': 'ARTISAN'})
    description = models.TextField()
    base_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    cap_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    services = models.ManyToManyField("services.Services", related_name="artisan_services")
    weekdays_only = models.BooleanField(default=False)
    weekends_only = models.BooleanField(default=False)
    weekdays_and_weekends = models.BooleanField(default=True)
    
    image1 = models.ImageField(
        translation.gettext_lazy('skill image 1'),
        upload_to='skill_images/',
        null=True,
        blank=True,
        storage=public_media_storage
    )
    image2 = models.ImageField(
        translation.gettext_lazy('skill image 2'),
        upload_to='skill_images/',
        null=True,
        blank=True,
        storage=public_media_storage
    )
    image3 = models.ImageField(
        translation.gettext_lazy('skill image 3'),
        upload_to='skill_images/',
        null=True,
        blank=True,
        storage=public_media_storage
    )
    
    def __str__(self):
        return self.artisan.first_name


class BankDetails(models.Model):
    artisan = models.OneToOneField(User, on_delete=models.CASCADE, limit_choices_to={'role': 'ARTISAN'})
    name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=10)
    account_name = models.CharField(max_length=350)
    payment_on_delivery = models.BooleanField(default=False)
    escrow_payment = models.BooleanField(default=False)
    
    def __str__(self):
        return self.account_name
    

class OnboardingLevel(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    level_1 = models.BooleanField(default=False)
    level_2 = models.BooleanField(default=False)
    level_3 = models.BooleanField(default=False)
    
    def __str__(self):
        return self.user.first_name


class Rating(models.Model):
    STAR_NO = [(i, i) for i in range(1, 6)]  # Using a 1-5 scale

    client = models.ForeignKey(User, related_name="rated_client", on_delete=models.CASCADE, limit_choices_to={'role': 'USER'})
    artisan = models.ForeignKey(User, related_name="artisan_rating", on_delete=models.CASCADE, limit_choices_to={'role': 'ARTISAN'})
    star = models.IntegerField(choices=STAR_NO, default=1)
    message = models.TextField()

    class Meta:
        indexes = [
            models.Index(fields=['artisan']),
            models.Index(fields=['client']),
        ]


class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('SIGNUP', 'Sign Up'),
        ('BOOKING', 'Booking'),
        ('RATING', 'Rating'),
        ('PASSWORD_RESET', 'Password Reset'),
        ('SKILL', 'Skill Added'),
        ('BANK_DETAILS', 'Bank Details'),
        ('AVAILABILITY', 'Availability'),
        ('PROFILE_UPDATE', 'Profile Update'),
    )
    
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.notification_type} for {self.recipient.email}"