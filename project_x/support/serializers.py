from rest_framework import serializers

from support.models import SupportMessage, SupportTicket

MAX_MESSAGE = 4000


def person(user):
    if user is None:
        return None
    return {"id": str(user.id), "name": f"{user.first_name} {user.last_name}".strip() or user.email,
            "email": user.email, "role": user.role}


class MessageSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportMessage
        fields = ("id", "from_staff", "author_name", "text", "created_at")
        read_only_fields = fields

    def get_author_name(self, message):
        if message.from_staff:
            # Users see first names only for staff: "Ngozi from Handwork Expert"
            first = message.author.first_name if message.author else ""
            return f"{first} from Handwork Expert".strip() if first else "Handwork Expert"
        return message.ticket.contact_name


class TicketSerializer(serializers.ModelSerializer):
    """A ticket as its requester sees it"""
    reference = serializers.CharField(read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    unread = serializers.BooleanField(source="user_unread", read_only=True)
    messages = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = ("id", "reference", "subject", "category", "category_label", "booking", "status", "unread",
                  "last_message_at", "resolved_at", "created_at", "messages")
        read_only_fields = fields

    def get_messages(self, ticket):
        if not self.context.get("with_messages"):
            return None
        return MessageSerializer(ticket.messages.select_related("author", "ticket__requester"), many=True).data


class NewTicketSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=[value for value, _ in SupportTicket.CATEGORY])
    subject = serializers.CharField(min_length=4, max_length=150, trim_whitespace=True)
    message = serializers.CharField(min_length=10, max_length=MAX_MESSAGE, trim_whitespace=True)
    booking = serializers.IntegerField(required=False, allow_null=True)


class ReplySerializer(serializers.Serializer):
    text = serializers.CharField(min_length=1, max_length=MAX_MESSAGE, trim_whitespace=True)


class StaffReplySerializer(ReplySerializer):
    # After replying: wait for the user (the usual), or resolve it straight away
    then = serializers.ChoiceField(choices=("WAITING", "RESOLVED"), default="WAITING")


class StaffTicketSerializer(serializers.ModelSerializer):
    """A ticket in the staff inbox"""
    reference = serializers.CharField(read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    contact = serializers.SerializerMethodField()
    assigned_to = serializers.SerializerMethodField()
    unread = serializers.BooleanField(source="staff_unread", read_only=True)
    preview = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = ("id", "reference", "subject", "category", "category_label", "source", "status", "priority",
                  "contact", "booking", "assigned_to", "unread", "preview", "last_message_at", "resolved_at",
                  "created_at", "messages")
        read_only_fields = fields

    def get_contact(self, ticket):
        user = ticket.requester
        return {
            "user": person(user),
            "name": ticket.contact_name,
            "email": ticket.contact_email,
            "phone": (user.contact if user else ticket.phone) or "",
        }

    def get_assigned_to(self, ticket):
        return person(ticket.assigned_to)

    def get_preview(self, ticket):
        last = ticket.messages.order_by("-created_at", "-id").first()
        return {"text": last.text[:160], "from_staff": last.from_staff} if last else None

    def get_messages(self, ticket):
        if not self.context.get("with_messages"):
            return None
        return [{**MessageSerializer(message).data,
                 # Staff see who on the team wrote it
                 "author_name": (f"{message.author.first_name} {message.author.last_name}".strip() if message.from_staff and message.author
                                 else MessageSerializer(message).data["author_name"])}
                for message in ticket.messages.select_related("author", "ticket__requester")]


class AssignSerializer(serializers.Serializer):
    staff = serializers.UUIDField(allow_null=True)


class TicketUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[value for value, _ in SupportTicket.STATUS], required=False)
    priority = serializers.ChoiceField(choices=[value for value, _ in SupportTicket.PRIORITY], required=False)
