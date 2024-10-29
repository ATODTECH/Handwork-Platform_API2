from django.contrib import admin
from .models import User, Skill, BankDetails, OnboardingLevel, Rating

# Register your models here.

# admin.site.register(ArtisanProfile)
admin.site.register(User)
admin.site.register(Skill)
admin.site.register(BankDetails)
admin.site.register(OnboardingLevel)
admin.site.register(Rating)