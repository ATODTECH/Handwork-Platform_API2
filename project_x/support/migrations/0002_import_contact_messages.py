"""Messages sent through the website's contact form before the Support inbox existed become tickets, so staff see them"""
from django.db import migrations


def import_contacts(apps, schema_editor):
    Contact = apps.get_model("contact", "Contact")
    User = apps.get_model("main", "User")
    SupportTicket = apps.get_model("support", "SupportTicket")
    SupportMessage = apps.get_model("support", "SupportMessage")
    for contact in Contact.objects.all().order_by("timestamp"):
        requester = User.objects.filter(email__iexact=contact.email).first()
        ticket = SupportTicket.objects.create(
            requester=requester, name=contact.name, email=contact.email, phone=contact.phoneNumber,
            source="CONTACT_FORM", category="OTHER", subject=f"Website message from {contact.name}"[:150],
            status="OPEN", staff_unread=True, last_message_at=contact.timestamp, created_at=contact.timestamp,
        )
        SupportMessage.objects.create(ticket=ticket, author=requester, from_staff=False, text=contact.message,
                                      created_at=contact.timestamp)


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0001_initial"),
        ("contact", "0001_initial"),
    ]

    operations = [migrations.RunPython(import_contacts, migrations.RunPython.noop)]
