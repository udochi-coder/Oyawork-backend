from django.urls import include, path
from rest_framework.routers import DefaultRouter

from messaging.views import ConversationViewSet

router = DefaultRouter()
router.register("conversations", ConversationViewSet, "conversation")
urlpatterns = [path("", include(router.urls))]
