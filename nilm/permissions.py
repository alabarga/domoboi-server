from rest_framework.permissions import BasePermission
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from django.conf import settings


class IsEdgeDevice(BasePermission):
    """Validates the static EDGE_API_TOKEN in the Authorization header.

    Returns 401 if the header is missing or the token is wrong.
    DRF defaults to 403 for permission failures; we override that here
    by raising the appropriate DRF exception in has_permission().
    """

    def has_permission(self, request, view):
        auth = request.META.get('HTTP_AUTHORIZATION', '')
        expected = f"Token {getattr(settings, 'EDGE_API_TOKEN', '')}"
        if not auth:
            raise NotAuthenticated("Authorization header missing.")
        if auth != expected:
            raise AuthenticationFailed("Invalid token.")
        return True
