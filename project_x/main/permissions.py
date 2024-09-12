from rest_framework import permissions

class IsArtisanPermission(permissions.BasePermission):
    message = 'You do not have the permissions to perform this action'
    
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'ARTISAN')