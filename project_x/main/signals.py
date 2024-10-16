from django.db.models.signals import post_save
from django.dispatch import receiver
import logging
from main.models import Skill
from services.models.services_models import Services

@receiver(post_save, sender=Skill)
def add_artisan_to_service(sender, instance, created, **kwargs):
    if created:
        print(f"Signal triggered for Skill ID: {instance.id}")  # Debugging line
        artisan = instance.artisan
        services = instance.services.all()

        for service in services:
            print(f"Adding artisan {artisan} to service {service}")  # Debugging line
            service.client.add(artisan)  # Add artisan to service's client field
            service.save()