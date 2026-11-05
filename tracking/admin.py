from django.contrib import admin

from .models import JobEvidence, LocationPing


@admin.register(LocationPing)
class LocationPingAdmin(admin.ModelAdmin):
    list_display = ("booking", "latitude", "longitude", "created_at")


@admin.register(JobEvidence)
class JobEvidenceAdmin(admin.ModelAdmin):
    list_display = ("booking", "kind", "uploaded_by", "created_at")
