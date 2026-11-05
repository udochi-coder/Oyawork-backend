from django.conf import settings
from django.db import models


class LocationPing(models.Model):
    """Worker position while en route, sent every few seconds by the app."""

    booking = models.ForeignKey("bookings.Booking", on_delete=models.CASCADE, related_name="location_pings")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    accuracy_m = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "location_ping"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["booking", "-created_at"])]


class JobEvidence(models.Model):
    KIND_CHOICES = [("before", "Before work"), ("after", "After work")]

    booking = models.ForeignKey("bookings.Booking", on_delete=models.CASCADE, related_name="evidence")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="after")
    photo = models.ImageField(upload_to="job_evidence/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_evidence"
        ordering = ["created_at"]
