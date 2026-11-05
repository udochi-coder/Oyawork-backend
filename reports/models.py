import secrets

from django.conf import settings
from django.db import models


def _ticket():
    return f"OW-{secrets.randbelow(90000) + 10000}"


class Dispute(models.Model):
    REASON_CHOICES = [
        ("job_not_completed", "Job not completed"),
        ("poor_quality", "Work not done properly"),
        ("left_early", "Worker left early"),
        ("property_damaged", "Property damaged"),
        ("safety_concern", "Safety concern"),
        ("payment_issue", "Payment issue"),
        ("misconduct", "Misconduct or harassment"),
        ("other", "Other issue"),
    ]
    STATUS_CHOICES = [
        ("submitted", "Complaint submitted"),
        ("payment_held", "Payment temporarily held"),
        ("evidence_review", "Evidence reviewed"),
        ("parties_contacted", "Both parties contacted"),
        ("resolved", "OyaWork final decision"),
    ]
    STATUS_ORDER = [s for s, _ in STATUS_CHOICES]
    RESOLUTION_CHOICES = [
        ("", "Pending"),
        ("release_worker", "Payment released to worker"),
        ("refund_employer", "Payment refunded to employer"),
        ("split", "Payment split"),
        ("no_action", "No payment change"),
    ]

    ticket = models.CharField(max_length=12, unique=True, default=_ticket)
    booking = models.ForeignKey("bookings.Booking", on_delete=models.CASCADE, related_name="disputes")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="disputes_raised")
    raised_by_role = models.CharField(max_length=10)
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    details = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="submitted")
    resolution = models.CharField(max_length=20, choices=RESOLUTION_CHOICES, blank=True, default="")
    resolution_note = models.TextField(blank=True)
    previous_booking_status = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "dispute"
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.ticket} - {self.get_reason_display()}"


class DisputeEvidence(models.Model):
    dispute = models.ForeignKey(Dispute, on_delete=models.CASCADE, related_name="evidence")
    file = models.FileField(upload_to="dispute_evidence/")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dispute_evidence"
