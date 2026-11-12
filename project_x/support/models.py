"""
Support tickets: customers and artisans ask for help from their dashboard, and website contact-form messages
arrive the same way. Staff reply, assign and resolve them in the admin console's Support inbox.
"""
from django.db import models
from django.utils import timezone

from main.models import User


class SupportTicket(models.Model):
    CATEGORY = (
        ("PAYMENT", "Payments and refunds"),
        ("BOOKING", "A booking"),
        ("REQUEST", "Requests and offers"),
        ("ACCOUNT", "My account"),
        ("VERIFICATION", "Artisan verification"),
        ("OTHER", "Something else"),
    )
    # OPEN: waiting on staff. WAITING: staff replied and are waiting on the user. RESOLVED: done (reopens on reply).
    STATUS = (("OPEN", "Open"), ("WAITING", "Waiting for the user"), ("RESOLVED", "Resolved"))
    PRIORITY = (("NORMAL", "Normal"), ("HIGH", "High"))
    SOURCE = (("APP", "Dashboard"), ("CONTACT_FORM", "Website contact form"))

    # Signed-in customers and artisans; contact-form tickets may have none and use the name and email given
    requester = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="support_tickets")
    name = models.CharField(max_length=200, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    category = models.CharField(max_length=15, choices=CATEGORY, default="OTHER")
    subject = models.CharField(max_length=150)
    booking = models.ForeignKey("services.BookArtisan", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    source = models.CharField(max_length=15, choices=SOURCE, default="APP")
    status = models.CharField(max_length=10, choices=STATUS, default="OPEN")
    priority = models.CharField(max_length=10, choices=PRIORITY, default="NORMAL")
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tickets",
                                    limit_choices_to={"is_staff": True})
    # Who still has to read the latest message
    staff_unread = models.BooleanField(default=True)
    user_unread = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_message_at"]
        indexes = [models.Index(fields=["status", "last_message_at"])]

    def __str__(self):
        return f"{self.reference}: {self.subject}"

    @property
    def reference(self):
        return f"HW-{self.pk:05d}" if self.pk else "HW-new"

    @property
    def contact_name(self):
        if self.requester:
            return f"{self.requester.first_name} {self.requester.last_name}".strip() or self.requester.email
        return self.name or self.email or "Website visitor"

    @property
    def contact_email(self):
        return self.requester.email if self.requester else self.email


class SupportMessage(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    # Null for a contact-form visitor
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    from_staff = models.BooleanField(default=False)
    text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]
