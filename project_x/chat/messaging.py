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


def sender_name(message):
    """Before a booking, a provider only learns the customer's first name"""
    sender = message.sender
    if message.booking_id is None and getattr(sender, 'role', None) == "USER":
        return sender.first_name
    return sender.get_full_name() or sender.email


HIDDEN_TEXT = "This message was removed by our team."


def shown_text(message):
    return HIDDEN_TEXT if message.hidden_at else message.text


def message_payload(message):
    """A message as the WebSocket sends it (the mobile app reads these keys)"""
    sender = message.sender
    return {
        'id': str(message.id),
        'sender': str(sender.id),
        'sender_name': sender_name(message),
        'sender_role': getattr(sender, 'role', 'UNKNOWN'),
        'text': shown_text(message),
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


# ── Request conversations (before an offer is accepted) ──────────────────────────
# A customer and one provider talk about a request: the provider asks questions before offering, or the
# customer asks about an offer. Open while the request takes offers; read-only after that. The chosen
# provider's conversation carries on in the booking's chat.

def request_group_name(request_id, provider_id):
    return f"request_chat_{request_id}_{provider_id}"


def request_is_visible_to_provider(job_request, provider):
    """The provider may look at this request (it's in their feed, sent to them, or they offered on it)"""
    if job_request.status == "DRAFT":
        return False
    if job_request.target_provider_id == provider.pk:
        return True
    if job_request.status == "OPEN" and job_request.visibility == "PUBLIC":
        return True
    # Once it stops taking offers: only if they offered or talked to the customer, to read that history
    from .models import Message
    return (job_request.offers.filter(provider=provider).exists()
            or Message.objects.filter(request=job_request, provider=provider).exists())


def request_thread_problem(job_request, user, provider):
    """
    Why `user` can't read the conversation between this request's customer and `provider`, or None.
    The customer can read any conversation on their request; a provider only their own.
    """
    from services.views.marketplace import is_active_provider
    if provider is None or provider.role != "ARTISAN":
        return "Choose the provider."
    if user.pk == job_request.customer_id:
        return None
    if user.pk != provider.pk:
        return "Not authorized"
    if not is_active_provider(user):
        return "Requests open up once our team has approved your account."
    if not request_is_visible_to_provider(job_request, user):
        return "Not authorized"
    return None


def can_message_request(job_request):
    return job_request.takes_offers


def check_request_message(job_request, user, provider, text):
    """Why this request message can't be sent, or None"""
    from .models import Message
    if not can_message_request(job_request):
        return "This request isn't taking offers any more, so the chat is read-only."
    # A customer can't cold-message any provider: only one who offered, wrote first, or was asked privately
    if user.pk == job_request.customer_id:
        invited = (job_request.target_provider_id == provider.pk
                   or job_request.offers.filter(provider=provider).exists()
                   or Message.objects.filter(request=job_request, provider=provider).exists())
        if not invited:
            return "You can message a provider once they've sent an offer or a question."
    if not text:
        return "Type a message first."
    if len(text) > MAX_MESSAGE_LENGTH:
        return f"Keep messages under {MAX_MESSAGE_LENGTH} characters."
    return None


def broadcast_request_message(message):
    try:
        async_to_sync(get_channel_layer().group_send)(
            request_group_name(message.request_id, message.provider_id),
            {'type': 'chat.message', 'message': message_payload(message)},
        )
    except Exception as error:
        logger.error(f"Couldn't broadcast message {message.id} for request {message.request_id}: {error}")
