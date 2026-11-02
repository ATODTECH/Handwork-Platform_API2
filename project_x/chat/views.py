from django.db.models import Count, F, Max, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied

from .messaging import broadcast, can_message, check_message, is_participant
from .models import Message
from .serializers import MessageSerializer
from services.models.job_models import BookArtisan


def get_booking(request, booking_id):
    booking = BookArtisan.objects.filter(id=booking_id).first()
    if booking is None:
        raise NotFound("Booking not found")
    if not is_participant(booking, request.user):
        raise PermissionDenied("Not authorized")
    return booking


class ChatViewSet(viewsets.ViewSet):
    """A booking's messages, for its customer and artisan"""
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, booking_id=None):
        get_booking(request, booking_id)
        messages = Message.objects.filter(booking_id=booking_id).select_related('sender').order_by('timestamp', 'id')
        return Response(MessageSerializer(messages, many=True).data)

    @action(detail=False, methods=['post'])
    def send(self, request, booking_id=None):
        booking = get_booking(request, booking_id)
        text = str(request.data.get('text') or '').strip()
        problem = check_message(booking, text)
        if problem:
            return Response({"detail": problem}, status=status.HTTP_400_BAD_REQUEST)

        message = Message.objects.create(booking=booking, sender=request.user, text=text)
        broadcast(message)
        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def read(self, request, booking_id=None):
        """Mark the other person's messages as read"""
        get_booking(request, booking_id)
        updated = (Message.objects.filter(booking_id=booking_id, is_read=False)
                   .exclude(sender=request.user).update(is_read=True))
        return Response({"updated": updated})


def person(user):
    if user is None:
        return None
    return {
        "id": str(user.id),
        "name": user.get_full_name() or user.email,
        "profile_image": user.profile_image.url if user.profile_image else None,
    }


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def conversations(request):
    """Every booking the user is part of, as a conversation, most recent activity first"""
    user = request.user
    # id breaks ties between messages saved in the same instant
    latest = Message.objects.filter(booking=OuterRef('pk')).order_by('-timestamp', '-id')
    bookings = (
        BookArtisan.objects.filter(Q(client=user) | Q(artisan=user), artisan__isnull=False)
        .select_related('client', 'artisan')
        .prefetch_related('services')
        .annotate(
            last_message_at=Max('messages__timestamp'),
            unread=Count('messages', filter=Q(messages__is_read=False) & ~Q(messages__sender=user)),
            last_text=Subquery(latest.values('text')[:1]),
            last_sender=Subquery(latest.values('sender')[:1]),
            activity=Coalesce(Max('messages__timestamp'), F('created_at')),
        )
        .order_by('-activity')
    )

    return Response([{
        "booking_id": booking.id,
        "status": booking.status,
        "services": [service.name for service in booking.services.all()],
        "scheduled_date": booking.scheduled_date,
        "created_at": booking.created_at,
        "with": person(booking.artisan if booking.client_id == user.pk else booking.client),
        "last_message": {
            "text": booking.last_text,
            "timestamp": booking.last_message_at,
            "mine": booking.last_sender == user.pk,
        } if booking.last_text is not None else None,
        "unread": booking.unread,
        "can_send": can_message(booking),
    } for booking in bookings])
