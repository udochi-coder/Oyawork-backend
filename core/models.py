from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from core.constants import ROLE_ADMIN, ROLE_CHOICES, ROLE_EMPLOYER, ROLE_WORKER


class UserManager(BaseUserManager):

    def get_by_natural_key(self, phone_number):
        return self.get(phone_number=phone_number)

    def create_user(self, phone_number, email=None, password=None, **extra_fields):
        if not phone_number:
            raise ValueError("Phone number is required")

        email = self.normalize_email(email) if email else None

        user = self.model(phone_number=phone_number, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", ROLE_ADMIN)
        return self.create_user(phone_number, email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    objects = UserManager()

    full_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=11, unique=True)
    # Email is optional in the sign-up screens; NULLs don't clash with the unique index.
    email = models.EmailField(unique=True, null=True, blank=True)
    phone_verified = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)

    # The role is the user's *current mode*. A person can hold both a worker and
    # an employer profile and switch between them (see accounts.views.SwitchModeView).
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_WORKER)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.phone_number})"

    @property
    def is_worker(self):
        return self.role == ROLE_WORKER

    @property
    def is_employer(self):
        return self.role == ROLE_EMPLOYER

    @property
    def first_name(self):
        return (self.full_name or "").split(" ")[0]


class Notification(models.Model):
    KIND_CHOICES = [
        ("job", "Job"),
        ("offer", "Offer"),
        ("booking", "Booking"),
        ("tracking", "Tracking"),
        ("payment", "Payment"),
        ("dispute", "Dispute"),
        ("review", "Review"),
        ("message", "Message"),
        ("system", "System"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="system")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    # Free-form pointer the frontend uses to deep-link, e.g. {"booking": 4}.
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_id}: {self.title}"


class PhoneOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="phone_otps")
    PURPOSE_CHOICES = [("verify", "Phone verification"), ("reset", "Password reset"), ("email", "Email verification")]
    purpose = models.CharField(max_length=10, choices=PURPOSE_CHOICES, default="verify")
    # For email verification: the address the code was sent to (becomes the account email once confirmed).
    target = models.CharField(max_length=254, blank=True)
    code_hash = models.CharField(max_length=128)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
