from django.urls import include, path
from rest_framework import routers

from core.views import NotificationViewSet, UserViewSet, health

router = routers.DefaultRouter()
router.register("users", UserViewSet)
router.register("notifications", NotificationViewSet, "notification")

urlpatterns = [
    path("health/", health, name="health"),
    path("", include(router.urls)),
]
