import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Message
from services.models.job_models import BookArtisan  # Adjust if path different

@database_sync_to_async
def get_booking(booking_id):
    return BookArtisan.objects.select_related('client', 'artisan').get(id=booking_id)

@database_sync_to_async
def create_message(booking, sender, text):
    return Message.objects.create(booking=booking, sender=sender, text=text)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.booking_id = self.scope['url_route']['kwargs']['booking_id']
        self.booking_group_name = f"chat_{self.booking_id}"

        user = self.scope["user"]
        if user.is_anonymous:
            await self.close(code=4001)
            return

        try:
            booking = await get_booking(self.booking_id)

            # Safe comparison using related objects (now async-safe via select_related)
            if user != booking.client and user != booking.artisan:
                await self.close(code=4003)  # Forbidden
                return

            await self.channel_layer.group_add(
                self.booking_group_name,
                self.channel_name
            )
            await self.accept()

        except BookArtisan.DoesNotExist:
            await self.close(code=4004)  # Not found

    async def disconnect(self, close_code):
        if hasattr(self, 'booking_group_name'):
            await self.channel_layer.group_discard(
                self.booking_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            text = data.get('text', '').strip()
            if not text:
                return

            user = self.scope["user"]
            booking = await get_booking(self.booking_id)

            message = await create_message(booking, user, text)

            await self.channel_layer.group_send(
                self.booking_group_name,
                {
                    "type": "chat_message",
                    "message": {
                        "id": message.id,
                        "sender_id": user.id,
                        "sender_name": user.get_full_name() or user.email,
                        "sender_role": getattr(user, 'role', 'UNKNOWN'),
                        "text": message.text,
                        "timestamp": message.timestamp.isoformat(),
                    }
                }
            )
        except Exception as e:
            # Optional: send error to client
            await self.send(text_data=json.dumps({
                "error": "Failed to send message"
            }))

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))