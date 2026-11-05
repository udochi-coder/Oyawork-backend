from django.conf import settings
from django.db import models

from core.constants import *  # noqa: F401,F403  (kept for backwards compatibility)
from core.models import User
from jobs.models import Skills

PROFILE_STATUS_CHOICES = [
    ("active", "Active"),
    ("banned", "Banned"),
    ("suspended", "Suspended"),
    ("pending", "Pending Verification"),
    ("deactivated", "Deactivated"),
]


class ProfileMixin(models.Model):
    """Fields and helpers shared by worker and employer profiles."""

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    review_count = models.PositiveIntegerField(default=0)
    agreed_terms_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def full_name(self):
        return self.user.full_name

    @property
    def email(self):
        return self.user.email

    @property
    def is_active(self):
        return self.status == "active"

    @property
    def is_verified(self):
        return self.status == "active"


class Employer(ProfileMixin):
    EMPLOYER_STATUS_CHOICES = PROFILE_STATUS_CHOICES
    EMPLOYER_TYPE_CHOICES = [
        ("household", "Household"),
        ("small_business", "Small_Business"),
        ("company", "Company"),
    ]

    profile_photo = models.ImageField(upload_to="profile_photos/employers/", blank=True, null=True)
    status = models.CharField(choices=EMPLOYER_STATUS_CHOICES, max_length=20, default="pending")
    employer_type = models.CharField(choices=EMPLOYER_TYPE_CHOICES, max_length=20, default="household")
    bio = models.TextField(blank=True)
    address = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    completed_jobs = models.PositiveIntegerField(default=0)
    preferred_radius = models.PositiveIntegerField(
        default=10,
        help_text="Maximum distance in kilometers the employer is willing to find a worker",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="employer_profile")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "employer_account"
        ordering = ["address"]

    def __str__(self):
        return f"{self.user.full_name} - Employer"


class Worker(ProfileMixin):
    WORKER_STATUS_CHOICES = PROFILE_STATUS_CHOICES
    AVAILABILITY_CHOICES = [
        ("available", "Available"),
        ("unavailable", "Unavailable"),
        ("busy", "Busy"),
    ]
    EXPERIENCE_LEVEL_CHOICES = [
        ("beginner", "Beginner (0-1 years)"),
        ("intermediate", "Intermediate (1-3 years)"),
        ("experienced", "Experienced (3+ years)"),
    ]

    profile_photo = models.ImageField(upload_to="profile_photos/workers/", blank=True, null=True)
    status = models.CharField(choices=WORKER_STATUS_CHOICES, max_length=20, default="pending")
    bio = models.TextField(blank=True)
    address = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    experience_years = models.PositiveIntegerField(default=0)
    experience_level = models.CharField(choices=EXPERIENCE_LEVEL_CHOICES, max_length=20, default="beginner")
    # Any of: available_now, weekdays, weekends, custom
    availability_schedule = models.JSONField(default=list, blank=True)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    reliability_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    availability_status = models.CharField(choices=AVAILABILITY_CHOICES, max_length=20, default="available")
    preferred_radius = models.PositiveIntegerField(
        default=10,
        help_text="Maximum distance in kilometers the worker is willing to travel",
    )
    completed_jobs = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="worker_profile")
    updated_at = models.DateTimeField(auto_now=True)
    skills = models.ManyToManyField(Skills, blank=True, related_name="workers")

    class Meta:
        db_table = "worker_account"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.full_name} - Worker"

    @property
    def is_online(self):
        return self.availability_status == "available"


class VerificationDocument(models.Model):
    DOC_TYPE_CHOICES = [
        ("nin", "National Identification Number (NIN)"),
        ("selfie", "Selfie for facial verification"),
        ("utility_bill", "Utility bill (NEPA, water or waste)"),
        ("government_document", "Government document"),
        ("other_proof", "Other accepted proof"),
    ]
    IDENTITY_TYPES = ("nin",)
    FACE_TYPES = ("selfie",)
    ADDRESS_TYPES = ("utility_bill", "government_document", "other_proof")

    STATUS_CHOICES = [
        ("pending", "Pending review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="verification_documents")
    doc_type = models.CharField(max_length=30, choices=DOC_TYPE_CHOICES)
    # NIN number, or a reference printed on the document.
    number = models.CharField(max_length=50, blank=True)
    file = models.FileField(upload_to="verification/", blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    note = models.CharField(max_length=255, blank=True)
    # What the automatic ID check (Dojah) returned, minus photos. Empty when not checked.
    provider_result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "verification_document"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} - {self.doc_type} ({self.status})"
