from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from services.catalog import CATALOG
from services.models.services_models import Category, Services

SERVICE_COUNT = len({name.lower() for entry in CATALOG for name, _ in entry["services"]})


def seed(*args):
    call_command("seed_catalog", *args, stdout=StringIO())


class SeedCatalogTests(TestCase):
    def test_creates_every_category_and_service(self):
        seed()
        self.assertEqual(Category.objects.count(), len(CATALOG))
        self.assertEqual(Services.objects.count(), SERVICE_COUNT)
        plumbing = Category.objects.get(name="Home And Property").service.filter(name="Plumbing")
        self.assertTrue(plumbing.exists())

    def test_running_twice_changes_nothing(self):
        seed()
        seed()
        self.assertEqual(Category.objects.count(), len(CATALOG))
        self.assertEqual(Services.objects.count(), SERVICE_COUNT)

    def test_keeps_rows_added_or_edited_by_hand(self):
        Services.objects.create(name="plumbing", description="Edited in admin")
        Category.objects.create(name="Automotive", description="Edited in admin")
        seed()
        self.assertEqual(Services.objects.filter(name__iexact="plumbing").count(), 1)
        self.assertEqual(Services.objects.get(name__iexact="plumbing").description, "Edited in admin")
        self.assertEqual(Category.objects.get(name="Automotive").description, "Edited in admin")
        self.assertTrue(Category.objects.get(name="Home And Property").service.filter(name="plumbing").exists())

    def test_dry_run_saves_nothing(self):
        seed("--dry-run")
        self.assertFalse(Category.objects.exists())
        self.assertFalse(Services.objects.exists())

    def test_services_endpoint_lists_the_catalogue(self):
        seed()
        response = self.client.get("/api/get_services")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]), SERVICE_COUNT)
