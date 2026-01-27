import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Message
from services.models.job_models import BookArtisan 
import logging

logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.booking_id = self.scope['url_route']['kwargs']['booking_id']
        self.user = self.scope.get('user')
        logger.info(f"WebSocket connect attempt for booking_id={self.booking_id}, user={getattr(self.user, 'email', 'Anonymous')}")

        # Accept connection immediately
        await self.accept()
        logger.info(f"WebSocket accepted for booking_id={self.booking_id}, user={getattr(self.user, 'email', 'Anonymous')}")

        # Check authentication
        if self.user.is_anonymous:
            logger.error(f"Authentication failed for booking_id={self.booking_id}")
            await self.send(text_data=json.dumps({'error': 'Authentication required'}))
            await self.close(code=4001)
            return

        # Verify booking exists
        try:
            self.booking = await database_sync_to_async(BookArtisan.objects.select_related('client', 'artisan').get)(id=self.booking_id)
        except BookArtisan.DoesNotExist:
            logger.error(f"Booking not found: {self.booking_id}")
            await self.send(text_data=json.dumps({'error': 'Booking not found'}))
            await self.close(code=4004)
            return

        # Authorization check
        if self.user != self.booking.client and self.user != self.booking.artisan:
            logger.error(f"User {self.user.email} not authorized for booking_id={self.booking_id}")
            await self.send(text_data=json.dumps({'error': 'Not authorized for this booking'}))
            await self.close(code=4002)
            return

        self.group_name = f"chat_{self.booking_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)

        # Send message history (safe JSON)
        messages = await self.get_messages()
        await self.send(text_data=json.dumps({'messages': messages}))
        logger.info(f"Sent {len(messages)} messages for booking_id={self.booking_id}")

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        logger.info(f"WebSocket disconnected for booking_id={self.booking_id}, code={close_code}")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            text = data.get('text', '').strip()
            if not text:
                await self.send(text_data=json.dumps({'error': 'Text required'}))
                return

            message = await database_sync_to_async(Message.objects.create)(
                booking=self.booking,
                sender=self.user,
                text=text
            )

            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'chat.message',
                    'message': {
                        'id': str(message.id),
                        'sender': str(self.user.id),
                        'sender_name': self.user.get_full_name() or self.user.email,
                        'sender_role': getattr(self.user, 'role', 'UNKNOWN'),
                        'text': text,
                        'timestamp': message.timestamp.isoformat(),
                    }
                }
            )
        except Exception as e:
            logger.error(f"Error in receive: {str(e)}")
            await self.send(text_data=json.dumps({'error': 'Failed to send message'}))

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def get_messages(self):
        messages = Message.objects.filter(booking_id=self.booking_id).order_by('timestamp')
        return [{
            'id': str(m.id), 
            'text': m.text,
            'sender': str(m.sender.id),
            'sender_name': m.sender.get_full_name() or m.sender.email,
            'timestamp': m.timestamp.isoformat(),
        } for m in messages]