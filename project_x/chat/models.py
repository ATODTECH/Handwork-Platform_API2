from django.db import models
from django.db.models import Q
from services.models.job_models import BookArtisan
from main.models import User


class Message(models.Model):
    """
    A chat message, in one of two conversations:
    - a booking's, between its customer and artisan (`booking`);
    - a request's, between its customer and one provider before an offer is accepted (`request` + `provider`).
    When an offer is accepted, that provider's request messages also get the new `booking`, so the booking's
    chat opens with the conversation that led to it.
    """
    booking = models.ForeignKey(
        BookArtisan,
        on_delete=models.CASCADE,
        related_name='messages',
        null=True,
        blank=True,
    )
    request = models.ForeignKey(
        "services.JobRequest", on_delete=models.CASCADE, related_name='messages', null=True, blank=True
    )
    # The provider side of a request conversation (the customer is the request's)
    provider = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+', null=True, blank=True)
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['timestamp']
        constraints = [
            models.CheckConstraint(
                check=Q(booking__isnull=False) | (Q(request__isnull=False) & Q(provider__isnull=False)),
                name='message_belongs_to_a_conversation',
            ),
        ]
        indexes = [models.Index(fields=['request', 'provider'])]

    def __str__(self):
        where = f"booking {self.booking_id}" if self.booking_id else f"request {self.request_id}"
        return f"Message from {self.sender} in {where}"
