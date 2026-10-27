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
