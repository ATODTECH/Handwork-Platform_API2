from django.db import models
from services.models.job_models import BookArtisan
from main.models import User

class Message(models.Model):
    booking = models.ForeignKey(
        BookArtisan,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"Message from {self.sender} in booking {self.booking.id}"