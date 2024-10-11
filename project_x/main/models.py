from django.contrib.auth.models import AbstractUser, AbstractBaseUser, BaseUserManager
import string, random
from django.db import models
from django.utils import timezone
from uuid import uuid4
from datetime import timedelta

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
    contact = models.CharField(max_length=15, unique=False)
    profile_image = models.ImageField(null=True)
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
    document = models.FileField(upload_to='onboarding_docs', null=True)
    document_type = models.CharField(choices=DOCUMENT_TYPE, max_length=16, null=True)
    role = models.CharField(max_length=7, choices=ROLE, default="USER")
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = CustomUserManager()
    
    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower()
            
        if not self.username:
            random_username = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
            self.username=random_username
        return super(User, self).save(*args, **kwargs)



class Skill(models.Model):
    artisan = models.OneToOneField(User, on_delete=models.CASCADE, limit_choices_to={'role': 'ARTISAN'})
    description = models.TextField()
    base_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    cap_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)  
    weekdays_only = models.BooleanField(default=False)
    weekends_only = models.BooleanField(default=False)
    weekdays_and_weekends = models.BooleanField(default=True)
    
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
    
    
    
# class ArtisanProfile(models.Model):
#     EXPERIENCE = (
#         ("1", "1"),
#         ("2", "2"),
#         ("3", "3"),
#         ("4 & Above", "4 & Above")
#     )
    
#     user = models.ForeignKey(User, on_delete=models.CASCADE, limit_choices_to={'role':'ARTISAN'})
#     address = models.CharField(max_length=450)
#     longitude = models.CharField(max_length=15)
#     latitude = models.CharField(max_length=15)
#     business_contact = models.CharField(max_length=15, unique=True)
#     service_type = models.ManyToManyField('services.Services')
#     years_of_experience = models.CharField(choices=EXPERIENCE, max_length=9, null=True)
    
#     def __str__(self):
#         return f"{self.user.first_name}"