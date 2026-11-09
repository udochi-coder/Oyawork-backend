from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

schema_view = get_schema_view(
    openapi.Info(
        title="OyaWork API",
        default_version="v1",
        description="Connecting verified menial job workers directly with employers in Lagos.",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

admin.site.site_header = "OyaWork admin"
admin.site.site_title = "OyaWork admin"

urlpatterns = [
    path("", RedirectView.as_view(url="/swagger/", permanent=False)),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    path("api/", include("accounts.urls")),
    path("api/", include("jobs.urls")),
    path("api/", include("bookings.urls")),
    path("api/", include("payments.urls")),
    path("api/", include("reports.urls")),
    path("api/", include("reviews.urls")),
    path("api/", include("messaging.urls")),
    path("swagger.<format>/", schema_view.without_ui(cache_timeout=0), name="schema-json"),
    path("swagger/", schema_view.with_ui("swagger", cache_timeout=0), name="schema-swagger-ui"),
    path("redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="schema-redoc"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


def server_error(request, *args, **kwargs):
    """API callers get JSON instead of Django's HTML error page. The full traceback is still in the Render logs."""
    from django.http import JsonResponse
    from django.views.defaults import server_error as html_server_error

    if request.path.startswith("/api/"):
        return JsonResponse({"error": "Something went wrong on our side. Please try again in a moment."}, status=500)
    return html_server_error(request, *args, **kwargs)


handler500 = server_error
