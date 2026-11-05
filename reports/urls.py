from django.urls import include, path
from rest_framework.routers import DefaultRouter

from reports.views import DisputeViewSet

router = DefaultRouter()
router.register("disputes", DisputeViewSet, "dispute")
urlpatterns = [path("", include(router.urls))]
