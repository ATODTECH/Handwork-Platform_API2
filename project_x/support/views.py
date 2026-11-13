"""
Help & support. Customers and artisans open tickets from their dashboard and reply to staff; staff work the
inbox. A reply from either side flips who still has to read it; a user replying reopens a resolved ticket.
"""
import logging

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from chat.messaging import is_participant
from main.emails import greeting, send_email
from main.models import Notification, User
from services.models.job_models import BookArtisan
from support.models import SupportMessage, SupportTicket
from support.serializers import (
    AssignSerializer, NewTicketSerializer, ReplySerializer, StaffReplySerializer, StaffTicketSerializer,
    TicketSerializer, TicketUpdateSerializer,
)

logger = logging.getLogger("services")

# Enough for anyone with real problems; stops one account flooding the inbox
MAX_OPEN_TICKETS = 10


def tell_requester(ticket, reply_text):
    """A staff reply reaches the user in the app and by email (contact-form visitors by email only)"""
    if ticket.requester:
        Notification.objects.create(
            recipient=ticket.requester, notification_type="SUPPORT",
            message=f"Handwork Expert replied to your support request \"{ticket.subject}\" ({ticket.reference}).",
        )
    email = ticket.contact_email
    if email:
        where = ("Reply from Help & support in your dashboard." if ticket.requester
                 else "To reply, send us another message through the contact form on our website.")
        hello = greeting(ticket.requester) if ticket.requester else (f"Hi {ticket.name.split()[0]}," if ticket.name else "Hi,")
        send_email(email, f"Re: {ticket.subject} [{ticket.reference}]",
                   f"{hello}\n\n{reply_text}\n\n{where}\n\nHandwork Expert support")


def add_message(ticket, author, text, from_staff):
    now = timezone.now()
    SupportMessage.objects.create(ticket=ticket, author=author, from_staff=from_staff, text=text, created_at=now)
    ticket.last_message_at = now
    ticket.staff_unread = not from_staff
    ticket.user_unread = from_staff


class TicketViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """GET/POST /api/support/tickets, GET …/{id}, POST …/{id}/messages, POST …/{id}/resolve"""
    permission_classes = [IsAuthenticated]
    serializer_class = TicketSerializer

    def get_queryset(self):
        return SupportTicket.objects.filter(requester=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        ticket = self.get_object()
        if ticket.user_unread:
            ticket.user_unread = False
            ticket.save(update_fields=["user_unread"])
        return Response(TicketSerializer(ticket, context={"with_messages": True}).data)

    def create(self, request):
        serializer = NewTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        booking = None
        if data.get("booking"):
            booking = BookArtisan.objects.filter(pk=data["booking"]).first()
            if booking is None or not is_participant(booking, request.user):
                return Response({"booking": ["Choose one of your own bookings."]}, status=status.HTTP_400_BAD_REQUEST)
        if self.get_queryset().exclude(status="RESOLVED").count() >= MAX_OPEN_TICKETS:
            return Response({"detail": "You have a lot of open requests. Reply on one of those, and we'll get to it."},
                            status=status.HTTP_400_BAD_REQUEST)
        ticket = SupportTicket(requester=request.user, category=data["category"], subject=data["subject"], booking=booking)
        ticket.save()
        add_message(ticket, request.user, data["message"], from_staff=False)
        ticket.save()
        logger.info(f"Support ticket {ticket.reference} opened by {request.user.email}")
        return Response(TicketSerializer(ticket, context={"with_messages": True}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        ticket = self.get_object()
        serializer = ReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        add_message(ticket, request.user, serializer.validated_data["text"], from_staff=False)
        # Replying to a resolved ticket opens it again, so it doesn't get lost
        ticket.status = "OPEN"
        ticket.resolved_at = None
        ticket.user_unread = False
        ticket.save()
        return Response(TicketSerializer(ticket, context={"with_messages": True}).data)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = "RESOLVED"
        ticket.resolved_at = timezone.now()
        ticket.staff_unread = False
        ticket.save()
        return Response(TicketSerializer(ticket, context={"with_messages": True}).data)

    @action(detail=False, methods=["get"])
    def unread(self, request):
        return Response({"count": self.get_queryset().filter(user_unread=True).count()})


STAFF_FILTERS = {
    "open": Q(status="OPEN"),
    "waiting": Q(status="WAITING"),
    "resolved": Q(status="RESOLVED"),
}


class AdminSupportViewSet(viewsets.ReadOnlyModelViewSet):
    """Every ticket (?status=open|waiting|resolved|mine, ?search=), `counts`, `reply`, `assign`, `update`"""
    permission_classes = [IsAdminUser]
    serializer_class = StaffTicketSerializer

    def get_queryset(self):
        queryset = SupportTicket.objects.select_related("requester", "assigned_to").prefetch_related("messages")
        status_filter = self.request.query_params.get("status", "")
        if status_filter == "mine":
            queryset = queryset.filter(assigned_to=self.request.user).exclude(status="RESOLVED")
        elif status_filter in STAFF_FILTERS:
            queryset = queryset.filter(STAFF_FILTERS[status_filter])
        search = self.request.query_params.get("search", "").strip()
        if search:
            matches = (Q(subject__icontains=search) | Q(email__icontains=search) | Q(name__icontains=search)
                       | Q(requester__email__icontains=search) | Q(requester__first_name__icontains=search)
                       | Q(requester__last_name__icontains=search))
            digits = search.upper().removeprefix("HW-").lstrip("0")
            if digits.isdigit():
                matches |= Q(pk=int(digits))
            queryset = queryset.filter(matches)
        # Urgent first, then the ones waiting longest
        return queryset.order_by("-priority", "-last_message_at") if status_filter != "resolved" else queryset.order_by("-resolved_at")

    def retrieve(self, request, *args, **kwargs):
        ticket = self.get_object()
        if ticket.staff_unread:
            ticket.staff_unread = False
            ticket.save(update_fields=["staff_unread"])
        return Response(self.serialize(ticket))

    def serialize(self, ticket):
        return StaffTicketSerializer(ticket, context={"with_messages": True}).data

    @action(detail=False, methods=["get"])
    def counts(self, request):
        totals = SupportTicket.objects.aggregate(
            all=Count("pk"), **{name: Count("pk", filter=condition) for name, condition in STAFF_FILTERS.items()},
            unread=Count("pk", filter=Q(staff_unread=True) & ~Q(status="RESOLVED")),
        )
        totals["mine"] = SupportTicket.objects.filter(assigned_to=request.user).exclude(status="RESOLVED").count()
        return Response(totals)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        ticket = self.get_object()
        serializer = StaffReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text, then = serializer.validated_data["text"], serializer.validated_data["then"]
        add_message(ticket, request.user, text, from_staff=True)
        ticket.status = then
        ticket.resolved_at = timezone.now() if then == "RESOLVED" else None
        if ticket.assigned_to_id is None:
            ticket.assigned_to = request.user
        ticket.save()
        tell_requester(ticket, text)
        logger.info(f"Support ticket {ticket.reference}: {request.user.email} replied ({then})")
        return Response(self.serialize(ticket))

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        ticket = self.get_object()
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff_id = serializer.validated_data["staff"]
        if staff_id is None:
            ticket.assigned_to = None
        else:
            ticket.assigned_to = get_object_or_404(User, pk=staff_id, is_staff=True, is_active=True)
        ticket.save(update_fields=["assigned_to", "updated_at"])
        return Response(self.serialize(ticket))

    @action(detail=True, methods=["post"], url_path="update")
    def update_ticket(self, request, pk=None):
        ticket = self.get_object()
        serializer = TicketUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "status" in data:
            ticket.status = data["status"]
            ticket.resolved_at = timezone.now() if data["status"] == "RESOLVED" else None
            if data["status"] == "RESOLVED":
                ticket.staff_unread = False
        if "priority" in data:
            ticket.priority = data["priority"]
        ticket.save()
        return Response(self.serialize(ticket))

