import hashlib
import hmac
import uuid
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from loguru import logger

from core.utils import money, notify
from payments.models import Escrow, PaymentIntent, Transaction, Wallet

PAYSTACK_BASE = "https://api.paystack.co"


class PaymentError(Exception):
    pass


def get_wallet(user, lock=False):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    if lock:
        wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    return wallet


def credit(user, amount, kind, description, booking=None, reference=""):
    with transaction.atomic():
        wallet = get_wallet(user, lock=True)
        wallet.balance = money(wallet.balance + Decimal(amount))
        wallet.save(update_fields=["balance", "updated_at"])
        return Transaction.objects.create(
            wallet=wallet, kind=kind, amount=money(amount), description=description, booking=booking, reference=reference
        )


def debit(user, amount, kind, description, booking=None, status="success", **extra):
    with transaction.atomic():
        wallet = get_wallet(user, lock=True)
        amount = money(amount)
        if wallet.balance < amount:
            raise PaymentError("Your OyaWork wallet balance is too low for this payment.")
        wallet.balance = money(wallet.balance - amount)
        wallet.save(update_fields=["balance", "updated_at"])
        return Transaction.objects.create(
            wallet=wallet, kind=kind, amount=-amount, description=description, booking=booking, status=status, **extra
        )


def pending_escrow_for(user):
    """Money held in escrow that involves this user (as payer or as payee)."""
    held = Escrow.objects.filter(status__in=["held", "frozen"])
    as_worker = held.filter(booking__hiring_request__worker__user=user).aggregate(s=Sum("amount"))["s"] or 0
    as_employer = held.filter(booking__hiring_request__job__employer__user=user).aggregate(s=Sum("amount"))["s"] or 0
    return money(as_worker), money(as_employer)


def new_reference(prefix="OW"):
    return f"{prefix}-{uuid.uuid4().hex[:16].upper()}"


def fund_escrow(booking, method, reference=""):
    """Mark the booking as paid: money is now held by OyaWork."""
    from bookings.services import on_escrow_funded

    with transaction.atomic():
        if Escrow.objects.filter(booking=booking).exists():
            return booking.escrow
        escrow = Escrow.objects.create(booking=booking, amount=booking.agreed_price, method=method, reference=reference)
        on_escrow_funded(booking)
    return escrow


def release_escrow(booking, to_worker_amount=None, to_employer_amount=None, outcome_status="released"):
    """Pay the worker (minus any platform fee) and/or refund the employer."""
    with transaction.atomic():
        escrow = Escrow.objects.select_for_update().get(booking=booking)
        if escrow.status not in ("held", "frozen"):
            raise PaymentError("This escrow has already been settled.")
        if to_worker_amount is None and to_employer_amount is None:
            to_worker_amount, to_employer_amount = escrow.amount, Decimal("0")
        to_worker_amount = money(to_worker_amount or 0)
        to_employer_amount = money(to_employer_amount or 0)
        if to_worker_amount + to_employer_amount != escrow.amount:
            raise PaymentError("Split must add up to the escrowed amount.")

        title = booking.job.title
        if to_worker_amount > 0:
            fee = money(to_worker_amount * Decimal(str(settings.PLATFORM_FEE_PERCENT)) / 100)
            label = f"{title} - {booking.job.location}".strip(" -")
            if fee > 0:
                label += f" (after ₦{fee:,.2f} OyaWork fee)"
            credit(booking.worker.user, to_worker_amount - fee, "earning", label[:255], booking)
        if to_employer_amount > 0:
            credit(booking.employer.user, to_employer_amount, "refund", f"Refund: {title}", booking)
        escrow.status = outcome_status
        escrow.released_at = timezone.now()
        escrow.save(update_fields=["status", "released_at"])
    return escrow


# --- Paystack ---------------------------------------------------------------

def paystack_enabled():
    return bool(settings.PAYSTACK_SECRET_KEY)


def _paystack(method, path, **kwargs):
    resp = requests.request(
        method,
        PAYSTACK_BASE + path,
        headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        timeout=20,
        **kwargs,
    )
    data = resp.json()
    if not resp.ok or not data.get("status"):
        raise PaymentError(data.get("message") or "Paystack request failed")
    return data["data"]


def start_checkout(user, amount, purpose, method, booking=None):
    """Create a Paystack checkout and return the PaymentIntent (with authorization_url)."""
    reference = new_reference("OWP")
    # Which Paystack payment options checkout offers (PAYSTACK_CHANNELS, default: bank transfer only).
    channels = settings.PAYSTACK_CHANNELS
    email = user.email or f"{user.phone_number}@users.oyawork.app"
    callback = f"{settings.FRONTEND_URL}/payments/callback"
    data = _paystack(
        "POST",
        "/transaction/initialize",
        json={
            "email": email,
            "amount": int(money(amount) * 100),  # kobo
            "reference": reference,
            "currency": "NGN",
            "channels": channels,
            "callback_url": callback,
            "metadata": {"purpose": purpose, "booking": booking.pk if booking else None, "user": user.pk},
        },
    )
    return PaymentIntent.objects.create(
        user=user, purpose=purpose, booking=booking, amount=money(amount), method=method,
        reference=reference, authorization_url=data["authorization_url"],
    )


def apply_successful_intent(intent):
    """Idempotently apply a successful payment."""
    with transaction.atomic():
        intent = PaymentIntent.objects.select_for_update().get(pk=intent.pk)
        if intent.status == "success":
            return intent
        intent.status = "success"
        intent.save(update_fields=["status"])
        if intent.purpose == "escrow" and intent.booking_id:
            fund_escrow(intent.booking, intent.method, intent.reference)
        elif intent.purpose == "wallet_topup":
            credit(intent.user, intent.amount, "deposit", "Wallet top-up", reference=intent.reference)
            notify(intent.user, f"Wallet funded with ₦{intent.amount:,.0f}", kind="payment")
    return intent


def verify_reference(reference):
    intent = PaymentIntent.objects.get(reference=reference)
    if intent.status == "success":
        return intent
    data = _paystack("GET", f"/transaction/verify/{reference}")
    if data.get("status") == "success" and Decimal(data["amount"]) / 100 >= intent.amount:
        return apply_successful_intent(intent)
    if data.get("status") in ("failed", "abandoned"):
        intent.status = "failed"
        intent.save(update_fields=["status"])
    return intent


def valid_paystack_signature(body: bytes, signature: str) -> bool:
    if not settings.PAYSTACK_SECRET_KEY or not signature:
        return False
    digest = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


def handle_webhook(payload):
    if payload.get("event") != "charge.success":
        return
    reference = payload.get("data", {}).get("reference")
    intent = PaymentIntent.objects.filter(reference=reference).first()
    if not intent:
        logger.warning(f"Paystack webhook for unknown reference {reference}")
        return
    try:
        verify_reference(reference)
    except Exception as exc:
        logger.exception(f"Webhook verification failed for {reference}: {exc}")
