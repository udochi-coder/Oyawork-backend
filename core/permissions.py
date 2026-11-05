from rest_framework.permissions import SAFE_METHODS, BasePermission

from core.constants import ROLE_ADMIN, ROLE_EMPLOYER, ROLE_WORKER


class IsWorker(BasePermission):
    message = "Switch to Worker mode to do this."

    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.role == ROLE_WORKER)


class IsEmployer(BasePermission):
    message = "Switch to Employer mode to do this."

    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.role == ROLE_EMPLOYER)


class ReadOnlyOrAdmin(BasePermission):
    """Anyone signed in can read; only staff can change."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user.is_authenticated and (request.user.is_staff or request.user.role == ROLE_ADMIN))


class IsWorkerCanReadEmployerCanWrite(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        role = getattr(user, "role", None)
        if request.method in SAFE_METHODS:
            return role in (ROLE_WORKER, ROLE_EMPLOYER, ROLE_ADMIN)
        return role in (ROLE_EMPLOYER, ROLE_ADMIN)
