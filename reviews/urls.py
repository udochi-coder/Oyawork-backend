from django.urls import include, path
from rest_framework.routers import DefaultRouter

from reviews.views import ReviewViewSet

router = DefaultRouter()
router.register("reviews", ReviewViewSet, "review")
urlpatterns = [path("", include(router.urls))]
