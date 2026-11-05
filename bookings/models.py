from django.conf import settings
from django.db import models

from accounts.models import Worker
from jobs.models import Jobs

PARTY_CHOICES = [("employer", "Employer"), ("worker", "Worker")]


class HiringRequest(models.Model):
    """One worker <-> one job conversation about price, before a booking exists.

    Created either by the employer (swipe-to-hire / "Hire" on a profile) or by a
    worker responding to a nearby job (accept at budget, or counter offer).
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("negotiating", "Negotiating"),
        ("accepted", "Accepted"),
        ("rejected", "Rejected"),
        ("cancelled", "Cancelled"),
        ("expired", "Expired"),
    ]
    OPEN_STATUSES = ("pending", "negotiating")

    job = models.ForeignKey(Jobs, on_delete=models.CASCADE, related_name="hiring_requests")
    worker = models.ForeignKey(Worker, on_delete=models.CASCADE, related_name="hiring_requests")
    initiated_by = models.CharField(max_length=10, choices=PARTY_CHOICES, default="employer")
    status = models.CharField(choices=STATUS_CHOICES, max_length=20, default="pending")
    current_amount = models.DecimalField(max_digits=10, decimal_places=2)
    last_offer_by = models.CharField(max_length=10, choices=PARTY_CHOICES, default="employer")
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hiring_request"
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["job", "worker"], name="unique_hiring_request_per_job_worker")]

    def __str__(self):
        return f"{self.job} <- {self.worker} ({self.status})"

    @property
    def employer(self):
        return self.job.employer

    def party_of(self, user):
        if user == self.job.employer.user:
            return "employer"
        if user == self.worker.user:
            return "worker"
        return None

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class Negotiation(models.Model):
    hiring_request = models.ForeignKey(HiringRequest, on_delete=models.CASCADE, related_name="negotiations")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="negotiations")
    sender_role = models.CharField(max_length=10, choices=PARTY_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "negotiation"
        ordering = ["created_at"]


class Booking(models.Model):
    STATUS_CHOICES = [
        ("pending_payment", "Awaiting payment"),
        ("scheduled", "Scheduled"),
        ("en_route", "En route"),
        ("arrived", "Arrived"),
        ("in_progress", "In progress"),
        ("completed", "Completed by worker"),
        ("confirmed", "Confirmed & paid"),
        ("cancelled", "Cancelled"),
        ("disputed", "Disputed"),
    ]
    ACTIVE_STATUSES = ("pending_payment", "scheduled", "en_route", "arrived", "in_progress", "completed", "disputed")
    DISPUTE_OUTCOMES = [
        ("", "None"),
        ("release_worker", "Released to worker"),
        ("refund_employer", "Refunded to employer"),
        ("split", "Split"),
    ]

    hiring_request = models.OneToOneField(HiringRequest, on_delete=models.CASCADE, related_name="booking")
    agreed_price = models.DecimalField(max_digits=10, decimal_places=2)
    scheduled_date = models.DateField()
    scheduled_time = models.TimeField()
    status = models.CharField(choices=STATUS_CHOICES, max_length=20, default="pending_payment")

    # "Who is this service for?"
    for_someone_else = models.BooleanField(default=False)
    recipient_name = models.CharField(max_length=100, blank=True)
    recipient_relationship = models.CharField(max_length=50, blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    service_address = models.CharField(max_length=255, blank=True)
    service_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    service_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # OyaCode: shown to the employer once the worker is en route; the worker types it in on arrival.
    oyacode = models.CharField(max_length=4, blank=True)
    oyacode_attempts = models.PositiveSmallIntegerField(default=0)

    en_route_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_role = models.CharField(max_length=10, choices=PARTY_CHOICES, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)
    dispute_outcome = models.CharField(max_length=20, choices=DISPUTE_OUTCOMES, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "booking"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Booking #{self.pk} {self.job.title} ({self.status})"

    @property
    def job(self):
        return self.hiring_request.job

    @property
    def worker(self):
        return self.hiring_request.worker

    @property
    def employer(self):
        return self.hiring_request.job.employer

    def party_of(self, user):
        return self.hiring_request.party_of(user)

    @property
    def destination(self):
        """Where the worker is heading: the recipient's address if given, else the job location."""
        if self.service_latitude is not None:
            return self.service_latitude, self.service_longitude
        return self.job.latitude, self.job.longitude
