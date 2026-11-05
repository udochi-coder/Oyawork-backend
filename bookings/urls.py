from django.urls import include, path
from rest_framework.routers import DefaultRouter

from bookings import views

router = DefaultRouter()
router.register("hiring-requests", views.HiringRequestViewSet, "hiring-request")
router.register("bookings", views.BookingViewSet, "booking")

urlpatterns = [path("", include(router.urls))]
