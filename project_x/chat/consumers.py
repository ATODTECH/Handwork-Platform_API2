import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .messaging import (
    check_message, check_request_message, group_name, is_participant, message_payload, request_group_name,
    request_thread_problem,
)
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


class RequestChatConsumer(AsyncWebsocketConsumer):
    """
    A request conversation's live chat, at ws/requests/<request_id>/<provider_id>/: the customer connects
    with the provider's id, the provider with their own. Same messages as ChatConsumer.
    """

    async def connect(self):
        kwargs = self.scope['url_route']['kwargs']
        self.request_id, self.provider_id = kwargs['request_id'], kwargs['provider_id']
        self.user = self.scope.get('user')
        await self.accept()

        if self.user.is_anonymous:
            await self.send(text_data=json.dumps({'error': 'Authentication required'}))
            await self.close(code=4001)
            return

        problem = await self.check_access()
        if problem:
            await self.send(text_data=json.dumps({'error': problem}))
            await self.close(code=4004 if problem == 'Request not found' else 4002)
            return

        self.group_name = request_group_name(self.request_id, self.provider_id)
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
            message, problem = await self.save_message(text)
            if problem:
                await self.send(text_data=json.dumps({'error': problem}))
                return
            await self.channel_layer.group_send(
                self.group_name, {'type': 'chat.message', 'message': message_payload(message)}
            )
        except Exception as e:
            logger.error(f"Error in receive for request {self.request_id}: {e}")
            await self.send(text_data=json.dumps({'error': 'Failed to send message'}))

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    def load(self):
        from main.models import User
        from services.models.marketplace_models import JobRequest
        from django.core.exceptions import ValidationError
        job_request = JobRequest.objects.filter(pk=self.request_id).first()
        try:
            provider = User.objects.filter(pk=self.provider_id).first()
        except ValidationError:
            provider = None
        return job_request, provider

    @database_sync_to_async
    def check_access(self):
        job_request, provider = self.load()
        if job_request is None:
            return 'Request not found'
        if provider is not None:
            # The same spelling of the id as REST broadcasts use, whatever the URL had
            self.provider_id = str(provider.pk)
        return request_thread_problem(job_request, self.user, provider)

    @database_sync_to_async
    def save_message(self, text):
        # Re-read the request: it may have stopped taking offers since this socket connected
        job_request, provider = self.load()
        problem = check_request_message(job_request, self.user, provider, text)
        if problem:
            return None, problem
        message = Message.objects.create(request=job_request, provider=provider, sender=self.user, text=text)
        return Message.objects.select_related('sender').get(pk=message.pk), None

    @database_sync_to_async
    def get_messages(self):
        messages = (Message.objects.filter(request_id=self.request_id, provider_id=self.provider_id)
                    .select_related('sender').order_by('timestamp', 'id'))
        return [message_payload(m) for m in messages]
