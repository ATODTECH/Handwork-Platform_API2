from rest_framework import serializers
from .models import Message


class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_role = serializers.CharField(source='sender.role', read_only=True)

    class Meta:
        model = Message
        fields = ('id', 'sender', 'sender_name', 'sender_role', 'text', 'timestamp', 'is_read')
        read_only_fields = ('timestamp', 'is_read')

    def get_sender_name(self, obj):
        return obj.sender.get_full_name() or obj.sender.email