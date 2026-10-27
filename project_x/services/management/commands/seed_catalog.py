from django.core.management.base import BaseCommand
from django.db import transaction

from services.catalog import CATALOG
from services.models.services_models import Category, Services


class Command(BaseCommand):
    help = "Create the starter categories and services. Safe to run repeatedly: existing rows are left as they are."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be created without saving.")

    @transaction.atomic
    def handle(self, *args, dry_run=False, **options):
        created_categories = created_services = linked = 0

        for entry in CATALOG:
            category, created = Category.objects.get_or_create(
                name=entry["name"], defaults={"description": entry["description"]}
            )
            created_categories += created
            if created:
                self.stdout.write(f"+ category {category.name}")

            existing_links = set(category.service.values_list("pk", flat=True))
            for name, description in entry["services"]:
                # Match case-insensitively so a service added by hand isn't duplicated
                service = Services.objects.filter(name__iexact=name).first()
                if service is None:
                    service = Services.objects.create(name=name, description=description)
                    created_services += 1
                    self.stdout.write(f"  + service {name}")
                if service.pk not in existing_links:
                    category.service.add(service)
                    linked += 1

        summary = f"{created_categories} categories, {created_services} services created; {linked} links added"
        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f"Dry run, nothing saved: {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
