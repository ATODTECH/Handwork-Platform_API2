"""
The admin console's Settings: each staff member's own profile and password, and (admins only) the staff list:
add someone, change their role, deactivate or reactivate them. Roles are in main/permissions.py.
"""
import logging
import secrets

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from main.emails import greeting, send_email
from main.models import User
from main.permissions import STAFF_ADMIN, STAFF_SUPPORT, IsStaffAdmin, has_superuser, staff_role

logger = logging.getLogger("main")

ROLES = (STAFF_ADMIN, STAFF_SUPPORT)


class StaffSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    is_you = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "first_name", "last_name", "email", "contact", "profile_image", "role", "is_active",
                  "is_you", "last_login", "date_joined")
        read_only_fields = fields

    def get_role(self, user):
        return staff_role(user) if user.is_active else (STAFF_ADMIN if user.is_superuser else STAFF_SUPPORT)

    def get_is_you(self, user):
        request = self.context.get("request")
        return bool(request and request.user.pk == user.pk)


class MyProfileSerializer(serializers.ModelSerializer):
    """What staff may change about themselves. Email is fixed: it's how they sign in."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "contact", "profile_image")

    def validate_first_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter your first name.")
        return value

    def validate_last_name(self, value):
        return value.strip()


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8, max_length=128)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": ["That isn't your current password."]})
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError({"new_password": ["Choose a password you haven't used here."]})
        try:
            validate_password(attrs["new_password"], user)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"new_password": list(error.messages)})
        return attrs


class NewStaffSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=250, trim_whitespace=True)
    last_name = serializers.CharField(max_length=250, trim_whitespace=True, allow_blank=True, required=False)
    email = serializers.EmailField()
    contact = serializers.CharField(max_length=15, trim_whitespace=True)
    role = serializers.ChoiceField(choices=ROLES)

    def validate_email(self, email):
        email = email.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return email

    def validate_contact(self, contact):
        if User.objects.filter(contact=contact).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return contact


class RoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ROLES)


def temporary_password():
    # Readable enough to type from an email, long enough to be safe until they change it
    return f"Hw-{secrets.token_urlsafe(9)}"


def is_last_admin(user):
    """The only active admin: demoting or deactivating them would leave nobody able to approve payouts"""
    if not user.is_superuser:
        return False
    return not User.objects.filter(is_staff=True, is_superuser=True, is_active=True).exclude(pk=user.pk).exists()


class MyStaffProfileViewSet(viewsets.ViewSet):
    """GET/PATCH /api/admin/me, POST /api/admin/me/password"""
    permission_classes = [IsAdminUser]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def list(self, request):
        return Response(StaffSerializer(request.user, context={"request": request}).data)

    def partial_update(self, request, pk=None):
        serializer = MyProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        old_image = request.user.profile_image.name if request.user.profile_image else None
        serializer.save()
        if old_image and "profile_image" in request.data and request.user.profile_image.name != old_image:
            request.user.profile_image.storage.delete(old_image)
        return Response(StaffSerializer(request.user, context={"request": request}).data)

    @action(detail=False, methods=["post"])
    def password(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        logger.info(f"Staff {request.user.email} changed their password")
        return Response({"detail": "Password changed."})


class AdminStaffViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Every staff account; admins add staff, change roles, deactivate and reactivate"""
    serializer_class = StaffSerializer

    def get_permissions(self):
        return [IsAdminUser()] if self.action == "list" else [IsStaffAdmin()]

    def get_queryset(self):
        return User.objects.filter(is_staff=True).order_by("-is_active", "-is_superuser", "first_name")

    def target(self, request, pk):
        user = self.get_queryset().filter(pk=pk).first() if pk else None
        if user is None:
            return None, Response({"detail": "We couldn't find that staff member."}, status=status.HTTP_404_NOT_FOUND)
        if user.pk == request.user.pk:
            return None, Response({"detail": "You can't change your own access. Ask another admin."},
                                  status=status.HTTP_400_BAD_REQUEST)
        return user, None

    def create(self, request):
        serializer = NewStaffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        password = temporary_password()
        # The first time roles are used, keep whoever is adding staff an admin
        with transaction.atomic():
            if not has_superuser():
                request.user.is_superuser = True
                request.user.save(update_fields=["is_superuser"])
            user = User(first_name=data["first_name"], last_name=data.get("last_name", ""), email=data["email"],
                        contact=data["contact"], role="USER", is_staff=True, is_superuser=data["role"] == STAFF_ADMIN)
            user.set_password(password)
            user.save()
        send_email(
            user.email,
            "You've been added to the Handwork Expert admin console",
            f"{greeting(user)}\n\n{request.user.first_name or 'An admin'} added you to the Handwork Expert admin console "
            f"as {'an Admin' if user.is_superuser else 'Support staff'}.\n\nSign in at {settings.ADMIN_LOGIN_URL}\n"
            f"Email: {user.email}\nTemporary password: {password}\n\n"
            "Please change your password in Settings after you sign in.\n\nHandwork Expert",
        )
        logger.info(f"Staff {user.email} ({staff_role(user)}) added by {request.user.email}")
        body = StaffSerializer(user, context={"request": request}).data
        # Shown once, in case the email doesn't arrive
        return Response({**body, "temporary_password": password}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def role(self, request, pk=None):
        user, error = self.target(request, pk)
        if error:
            return error
        serializer = RoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        make_admin = serializer.validated_data["role"] == STAFF_ADMIN
        if not make_admin and is_last_admin(user):
            return Response({"detail": "They're the only admin. Make someone else an admin first."},
                            status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            if not has_superuser():
                request.user.is_superuser = True
                request.user.save(update_fields=["is_superuser"])
            user.is_superuser = make_admin
            user.save(update_fields=["is_superuser"])
        logger.info(f"Staff {user.email} made {serializer.validated_data['role']} by {request.user.email}")
        return Response(StaffSerializer(user, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user, error = self.target(request, pk)
        if error:
            return error
        if not user.is_active:
            return Response({"detail": "This account is already deactivated."}, status=status.HTTP_400_BAD_REQUEST)
        if is_last_admin(user):
            return Response({"detail": "They're the only admin. Make someone else an admin first."},
                            status=status.HTTP_400_BAD_REQUEST)
        user.is_active = False
        user.save(update_fields=["is_active"])
        logger.info(f"Staff {user.email} deactivated by {request.user.email}")
        return Response(StaffSerializer(user, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        user, error = self.target(request, pk)
        if error:
            return error
        user.is_active = True
        user.save(update_fields=["is_active"])
        logger.info(f"Staff {user.email} reactivated by {request.user.email}")
        return Response(StaffSerializer(user, context={"request": request}).data)
