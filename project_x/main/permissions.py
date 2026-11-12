from rest_framework import permissions

class IsArtisanPermission(permissions.BasePermission):
    message = 'You do not have the permissions to perform this action'
    
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'ARTISAN')
    

class IsUserPermission(permissions.BasePermission):
    message = 'You do not have the permissions to perform this action'
    
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'USER')

class IsSelfOrStaff(permissions.BasePermission):
    """Object-level: a user may only read or change their own account (staff may manage any)."""
    message = 'You can only access your own account'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return obj == request.user or request.user.is_staff


class IsRatingAuthor(permissions.BasePermission):
    """Object-level: only the customer who wrote a rating may change or delete it."""
    message = 'You can only change your own ratings'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return obj.client_id == request.user.pk


class IsStaffOrReadOnly(permissions.BasePermission):
    """Anyone may read; only staff may create, change or delete."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


# Staff roles. Admins can do everything; Support staff can't make money decisions (approve payouts, refund or
# pay out a reported problem) or manage staff. Admin is Django's is_superuser, so no extra field is needed.
STAFF_ADMIN, STAFF_SUPPORT = "ADMIN", "SUPPORT"


def has_superuser():
    from main.models import User
    return User.objects.filter(is_staff=True, is_superuser=True, is_active=True).exists()


def staff_role(user):
    """ADMIN or SUPPORT. Until any active staff account is an admin, every staff member counts as one, so
    nobody is locked out of payouts when roles are first introduced."""
    if not (user and user.is_authenticated and user.is_staff):
        return None
    if user.is_superuser or not has_superuser():
        return STAFF_ADMIN
    return STAFF_SUPPORT


class IsStaffAdmin(permissions.BasePermission):
    """Staff with the Admin role"""
    message = "Only admins can do this. Ask an admin on your team."

    def has_permission(self, request, view):
        return staff_role(request.user) == STAFF_ADMIN
