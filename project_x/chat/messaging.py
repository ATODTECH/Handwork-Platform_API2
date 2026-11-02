"""
Rules shared by the chat's REST views and WebSocket consumer.

Each booking has one conversation between its customer and artisan. It opens with the request and
becomes read-only once the booking is closed. Staff can read it (admin bookings) but never write.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger('chat')

# Old deposit bookings end at "Paid in Full"
CLOSED_STATUSES = ("Declined", "Cancelled", "Released", "Refunded", "Paid in Full")
MAX_MESSAGE_LENGTH = 2000


def is_participant(booking, user):
    return user.is_authenticated and user.pk in (booking.client_id, booking.artisan_id)


def can_message(booking):
    return booking.status not in CLOSED_STATUSES


def check_message(booking, text):
    """The reason this message can't be sent, or None"""
    if not can_message(booking):
        return "This booking is closed, so the chat is read-only."
    if not text:
        return "Type a message first."
    if len(text) > MAX_MESSAGE_LENGTH:
        return f"Keep messages under {MAX_MESSAGE_LENGTH} characters."
    return None


def group_name(booking_id):
    return f"chat_{booking_id}"


def message_payload(message):
    """A message as the WebSocket sends it (the mobile app reads these keys)"""
    sender = message.sender
    return {
        'id': str(message.id),
        'sender': str(sender.id),
        'sender_name': sender.get_full_name() or sender.email,
        'sender_role': getattr(sender, 'role', 'UNKNOWN'),
        'text': message.text,
        'timestamp': message.timestamp.isoformat(),
        'is_read': message.is_read,
    }


def broadcast(message):
    """Push a message sent over REST to everyone connected to the booking's WebSocket"""
    try:
        async_to_sync(get_channel_layer().group_send)(
            group_name(message.booking_id), {'type': 'chat.message', 'message': message_payload(message)}
        )
    except Exception as error:
        # The message is saved either way; clients also refetch
        logger.error(f"Couldn't broadcast message {message.id} for booking {message.booking_id}: {error}")
