from rest_framework import serializers
from .messaging import sender_name, shown_text
from .models import Message


class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_role = serializers.CharField(source='sender.role', read_only=True)
    text = serializers.SerializerMethodField()
    hidden = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ('id', 'sender', 'sender_name', 'sender_role', 'text', 'hidden', 'timestamp', 'is_read')
        read_only_fields = ('timestamp', 'is_read')

    def get_sender_name(self, obj):
        return sender_name(obj)

    def get_text(self, obj):
        return shown_text(obj)

    def get_hidden(self, obj):
        return obj.hidden_at is not None

    def to_representation(self, obj):
        representation = super().to_representation(obj)
        # Staff still see what a removed message said
        if self.context.get("staff") and obj.hidden_at:
            representation["original_text"] = obj.text
        return representation
