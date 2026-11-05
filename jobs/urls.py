from django.urls import include, path
from rest_framework.routers import DefaultRouter

from jobs import views

router = DefaultRouter()
router.register("categories", views.CategoryViewSet, "category")
router.register("skills", views.SkillViewSet, "skill")
router.register("jobs", views.JobViewSet, "job")

urlpatterns = [path("", include(router.urls))]
