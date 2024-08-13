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
    # User Information
    id = models.UUIDField(default=uuid4, primary_key=True)
    first_name = models.CharField(max_length=250)
    last_name = models.CharField(max_length=250)
    email = models.EmailField(max_length=254, unique=True)
    password = models.CharField(max_length=128)
    contact = models.CharField(max_length=15, unique=False)
    profile_image = models.ImageField(null=True)
    is_artisan = models.BooleanField(default=False)
    is_professional = models.BooleanField(default=False)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = CustomUserManager()
    
    def save(self, *args, **kwargs):
        if not self.username:
            random_username = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
            self.username=random_username
        return super(User, self).save(*args, **kwargs)


class ArtisanProfile(models.Model):
    EXPERIENCE = (
        ("1", "1"),
        ("2", "2"),
        ("3", "3"),
        ("4 & Above", "4 & Above")
    )
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    address = models.CharField(max_length=450)
    longitude = models.CharField(max_length=15)
    latitude = models.CharField(max_length=15)
    business_contact = models.CharField(max_length=15, unique=True)
    service_type = models.ManyToManyField('services.Services')
    years_of_experience = models.CharField(choices=EXPERIENCE, max_length=9, null=True)
    
    def __str__(self):
        return f"{self.user.first_name}"