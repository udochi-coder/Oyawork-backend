from django.conf import settings
from django.db import models


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "wallet"

    def __str__(self):
        return f"{self.user} - ₦{self.balance}"


class Transaction(models.Model):
    KIND_CHOICES = [
        ("deposit", "Wallet top-up"),
        ("escrow_payment", "Escrow payment"),
        ("earning", "Job earning"),
        ("refund", "Refund"),
        ("withdrawal", "Withdrawal"),
        ("fee", "Platform fee"),
    ]
    STATUS_CHOICES = [("pending", "Pending"), ("success", "Success"), ("failed", "Failed")]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    # Positive = money into the wallet, negative = money out.
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="success")
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    booking = models.ForeignKey("bookings.Booking", null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    # For withdrawals
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=20, blank=True)
    account_name = models.CharField(max_length=100, blank=True)
    bank_code = models.CharField(max_length=20, blank=True)
    # Paystack Transfers: set when the payout was sent automatically.
    transfer_code = models.CharField(max_length=60, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wallet_transaction"
        ordering = ["-created_at"]


class Escrow(models.Model):
    STATUS_CHOICES = [
        ("held", "Held"),
        ("frozen", "Frozen (dispute)"),
        ("released", "Released to worker"),
        ("refunded", "Refunded to employer"),
        ("split", "Split"),
    ]
    METHOD_CHOICES = [("card", "Debit card (Paystack)"), ("bank_transfer", "Bank transfer"), ("wallet", "OyaWork wallet")]

    booking = models.OneToOneField("bookings.Booking", on_delete=models.CASCADE, related_name="escrow")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="held")
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "escrow"


class PaymentIntent(models.Model):
    """A Paystack checkout we're waiting on (escrow funding or wallet top-up)."""

    PURPOSE_CHOICES = [("escrow", "Escrow for booking"), ("wallet_topup", "Wallet top-up")]
    STATUS_CHOICES = [("pending", "Pending"), ("success", "Success"), ("failed", "Failed")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_intents")
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    booking = models.ForeignKey("bookings.Booking", null=True, blank=True, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, default="card")
    reference = models.CharField(max_length=100, unique=True)
    authorization_url = models.URLField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payment_intent"
        ordering = ["-created_at"]
