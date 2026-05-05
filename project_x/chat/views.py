from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied
from .models import Message
from .serializers import MessageSerializer
from services.models.job_models import BookArtisan


class ChatViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, booking_id=None):
        try:
            booking = BookArtisan.objects.get(id=booking_id)
        except BookArtisan.DoesNotExist:
            raise NotFound("Booking not found")

        if request.user not in [booking.client, booking.artisan]:
            raise PermissionDenied("Not authorized")

        messages = Message.objects.filter(booking_id=booking_id)
        serializer = MessageSerializer(messages, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def send(self, request, booking_id=None):
        try:
            booking = BookArtisan.objects.get(id=booking_id)
        except BookArtisan.DoesNotExist:
            raise NotFound("Booking not found")

        if request.user not in [booking.client, booking.artisan]:
            raise PermissionDenied("Not authorized")

        text = request.data.get('text')
        if not text:
            return Response({"detail": "Text required"}, status=400)

        message = Message.objects.create(
            booking=booking,
            sender=request.user,
            text=text
        )
        return Response(MessageSerializer(message).data, status=201)


# from rest_framework import viewsets, permissions
# from rest_framework.decorators import action
# from rest_framework.response import Response
# from .models import Message
# from .serializers import MessageSerializer
# from services.models.job_models import BookArtisan

# class ChatViewSet(viewsets.ViewSet):
#     permission_classes = [permissions.IsAuthenticated]

#     def list(self, request, booking_id=None):
#         booking = BookArtisan.objects.get(id=booking_id)
#         if request.user not in [booking.client, booking.artisan]:
#             return Response({"detail": "Not authorized"}, status=403)

#         messages = Message.objects.filter(booking_id=booking_id)
#         serializer = MessageSerializer(messages, many=True)
#         return Response(serializer.data)

#     @action(detail=False, methods=['post'])
#     def send(self, request, booking_id=None):
#         booking = BookArtisan.objects.get(id=booking_id)
#         if request.user not in [booking.client, booking.artisan]:
#             return Response({"detail": "Not authorized"}, status=403)

#         text = request.data.get('text')
#         if not text:
#             return Response({"detail": "Text required"}, status=400)

#         message = Message.objects.create(
#             booking=booking,
#             sender=request.user,
#             text=text
#         )
#         return Response(MessageSerializer(message).data, status=201)