import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .messaging import check_message, group_name, is_participant, message_payload
from .models import Message
from services.models.job_models import BookArtisan
import logging

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    """
    One booking's live chat. Sends `{messages: [...]}` (the history) on connect, then
    `{type: "chat.message", message}` for each new message, or `{error}`.
    """

    async def connect(self):
        self.booking_id = self.scope['url_route']['kwargs']['booking_id']
        self.user = self.scope.get('user')

        # Accept first, so the client receives the reason when we close
        await self.accept()

        if self.user.is_anonymous:
            await self.send(text_data=json.dumps({'error': 'Authentication required'}))
            await self.close(code=4001)
            return

        booking = await self.get_booking()
        if booking is None:
            await self.send(text_data=json.dumps({'error': 'Booking not found'}))
            await self.close(code=4004)
            return

        if not is_participant(booking, self.user):
            logger.warning(f"User {self.user.email} not authorized for chat on booking {self.booking_id}")
            await self.send(text_data=json.dumps({'error': 'Not authorized for this booking'}))
            await self.close(code=4002)
            return

        self.group_name = group_name(self.booking_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.send(text_data=json.dumps({'messages': await self.get_messages()}))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            text = str(json.loads(text_data).get('text') or '').strip()
        except (ValueError, AttributeError):
            await self.send(text_data=json.dumps({'error': 'Invalid message'}))
            return

        try:
            # Re-read the booking: it may have closed since this socket connected
            booking = await self.get_booking()
            problem = check_message(booking, text)
            if problem:
                await self.send(text_data=json.dumps({'error': problem}))
                return

            message = await self.save_message(booking, text)
            await self.channel_layer.group_send(
                self.group_name, {'type': 'chat.message', 'message': message_payload(message)}
            )
        except Exception as e:
            logger.error(f"Error in receive for booking {self.booking_id}: {e}")
            await self.send(text_data=json.dumps({'error': 'Failed to send message'}))

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def get_booking(self):
        return BookArtisan.objects.filter(id=self.booking_id).first()

    @database_sync_to_async
    def save_message(self, booking, text):
        return Message.objects.create(booking=booking, sender=self.user, text=text)

    @database_sync_to_async
    def get_messages(self):
        messages = Message.objects.filter(booking_id=self.booking_id).select_related('sender').order_by('timestamp', 'id')
        return [message_payload(m) for m in messages]
