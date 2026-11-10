from django.db.models import Count, F, Max, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied

from .messaging import broadcast, can_message, can_message_request, check_message, is_participant
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

    items = [{
        "kind": "booking",
        "booking_id": booking.id,
        "request_id": None,
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
        "activity": booking.activity,
    } for booking in bookings]
    items += request_conversations(user)
    items.sort(key=lambda item: item["activity"], reverse=True)
    for item in items:
        del item["activity"]
    return Response(items)


def request_conversations(user):
    """
    Conversations about requests (before an offer is accepted) that have at least one message. The chosen
    provider's carries on in the booking, so it's listed there instead.
    """
    from services.models.marketplace_models import JobRequest
    from services.serializers.marketplace_serializers import request_stage
    from main.models import User

    latest = Message.objects.filter(
        request=OuterRef('request_id'), provider=OuterRef('provider_id')
    ).order_by('-timestamp', '-id')
    threads = list(
        Message.objects.filter(request__isnull=False)
        .filter(Q(request__customer=user) | Q(provider=user))
        .values('request_id', 'provider_id')
        .annotate(
            last_message_at=Max('timestamp'),
            unread=Count('id', filter=Q(is_read=False) & ~Q(sender=user)),
            last_text=Subquery(latest.values('text')[:1]),
            last_sender=Subquery(latest.values('sender')[:1]),
        )
    )
    requests = JobRequest.objects.select_related('customer', 'booking', 'accepted_offer').prefetch_related('services')
    requests = {job_request.pk: job_request for job_request in requests.filter(pk__in={t['request_id'] for t in threads})}
    providers = {provider.pk: provider for provider in User.objects.filter(pk__in={t['provider_id'] for t in threads})}

    items = []
    for thread in threads:
        job_request, provider = requests[thread['request_id']], providers[thread['provider_id']]
        accepted = job_request.accepted_offer
        if job_request.booking_id and accepted and accepted.provider_id == provider.pk:
            continue
        items.append({
            "kind": "request",
            "booking_id": None,
            "request_id": job_request.id,
            "request_title": job_request.title,
            "provider_id": str(provider.pk),
            "status": request_stage(job_request),
            "services": [service.name for service in job_request.services.all()],
            "scheduled_date": job_request.deadline,
            "created_at": job_request.created_at,
            "with": person(provider) if job_request.customer_id == user.pk else {
                **person(job_request.customer), "name": job_request.customer.first_name,
            },
            "last_message": {
                "text": thread['last_text'],
                "timestamp": thread['last_message_at'],
                "mine": thread['last_sender'] == user.pk,
            },
            "unread": thread['unread'],
            "can_send": can_message_request(job_request),
            "activity": thread['last_message_at'],
        })
    return items
