from django.db.models.signals import post_save
from django.dispatch import receiver
import logging
from main.models import Skill, BankDetails, User, OnboardingLevel
from services.models.services_models import Services
from django.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)

@receiver(post_save, sender=BankDetails)
def mark_onboarding(sender, instance, created, **kwargs):
    if created and instance.role == "ARTISAN" and not instance.artisan.onboardingComplete:
        artisan_id = instance.artisan
        user = get_object_or_404(User, id=artisan_id)
        user.onboardingComplete = True
        user.save()
        logger.info(f"User {user.id} onboarded successfully")


@receiver(post_save, sender=User)
def mark_onboarding_for_user(sender, instance, created, **kwargs):
    if created and instance.role == "USER" and not instance.onboardingComplete:
        instance.onboardingComplete = True
        instance.save(update_fields=['onboardingComplete'])  # Only update this field
        logger.info(f"User {instance.id} onboarded successfully")


@receiver(post_save, sender=User)
def mark_onboarding_level_1(sender, instance, created, **kwargs):
    if created and instance.role == "ARTISAN":
        user = instance
        onboarding = OnboardingLevel.objects.get(user=user)
        onboarding.level_1 = True
        onboarding.save()
        logger.info(f"Onboarding Level One Check!")


@receiver(post_save, sender=Skill)
def mark_onboarding_level_2(sender, instance, created, **kwargs):
    if created:
        user = instance.artisan
        onboarding = OnboardingLevel.objects.get(user=user)
        onboarding.level_2 = True
        onboarding.save()
        logger.info(f"Onboarding Level Two Check!")


@receiver(post_save, sender=BankDetails)
def mark_onboarding_level_3(sender, instance, created, **kwargs):
    if created:
        user = instance.artisan
        onboarding = OnboardingLevel.objects.get(user=user)
        onboarding.level_3 = True
        onboarding.save()
        logger.info(f"Onboarding Level Three Check!")