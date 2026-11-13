"""
Contact-form messages now go to the website's email instead of Support (decided 12 Nov), so the tickets that
0002 copied from them are removed. Only tickets that came from the contact form; the originals stay in
contact.Contact. Tickets customers and artisans opened from their dashboard are untouched.
"""
from django.db import migrations


def remove_contact_form_tickets(apps, schema_editor):
    SupportTicket = apps.get_model("support", "SupportTicket")
    # Their messages go with them (SupportMessage.ticket cascades)
    SupportTicket.objects.filter(source="CONTACT_FORM").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0002_import_contact_messages"),
    ]

    operations = [migrations.RunPython(remove_contact_form_tickets, migrations.RunPython.noop)]
