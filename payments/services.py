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


# --- Paystack Transfers (automatic withdrawals) -------------------------------

def transfers_enabled():
    return paystack_enabled() and settings.PAYSTACK_TRANSFERS


def list_banks():
    """Nigerian banks Paystack can pay into: [{"name", "code"}], cached for a day."""
    from django.core.cache import cache

    banks = cache.get("paystack_banks")
    if banks is None:
        data = _paystack("GET", "/bank", params={"country": "nigeria", "currency": "NGN", "perPage": 500})
        banks = sorted(({"name": b["name"], "code": b["code"]} for b in data if b.get("active", True) and not b.get("is_deleted")),
                       key=lambda b: b["name"].lower())
        cache.set("paystack_banks", banks, 24 * 3600)
    return banks


def resolve_account(account_number, bank_code):
    """Returns the account holder's name, or raises PaymentError."""
    data = _paystack("GET", "/bank/resolve", params={"account_number": account_number, "bank_code": bank_code})
    return data.get("account_name", "")


def _fail_withdrawal(txn, reason):
    """Mark a withdrawal failed and put the money back in the wallet (once)."""
    with transaction.atomic():
        txn = Transaction.objects.select_for_update().get(pk=txn.pk)
        if txn.status == "failed":
            return txn
        txn.status = "failed"
        txn.failure_reason = (reason or "")[:255]
        txn.save(update_fields=["status", "failure_reason"])
        user = txn.wallet.user
        credit(user, -txn.amount, "refund", f"Withdrawal returned: {txn.reference}", reference=txn.reference)
    notify(user, f"Withdrawal of ₦{-txn.amount:,.0f} didn't go through",
           "The money is back in your OyaWork wallet. Check your bank details and try again.", kind="payment")
    return txn


def _succeed_withdrawal(txn):
    with transaction.atomic():
        txn = Transaction.objects.select_for_update().get(pk=txn.pk)
        if txn.status != "pending":
            return txn
        txn.status = "success"
        txn.save(update_fields=["status"])
    notify(txn.wallet.user, f"₦{-txn.amount:,.0f} sent to your bank",
           f"{txn.bank_name} {txn.account_number}", kind="payment")
    return txn


def payout_withdrawal(txn):
    """Send a pending withdrawal to the person's bank with Paystack Transfers.
    Paystack confirms with a transfer.success / transfer.failed webhook."""
    try:
        recipient = _paystack("POST", "/transferrecipient", json={
            "type": "nuban", "name": txn.account_name or txn.wallet.user.full_name,
            "account_number": txn.account_number, "bank_code": txn.bank_code, "currency": "NGN",
        })
        data = _paystack("POST", "/transfer", json={
            "source": "balance",
            "amount": int(-txn.amount * 100),  # kobo
            "recipient": recipient["recipient_code"],
            "reference": txn.reference.lower(),
            "reason": "OyaWork wallet withdrawal",
            "currency": "NGN",
        })
    except Exception as exc:
        logger.error(f"Paystack transfer failed for {txn.reference}: {exc}")
        return _fail_withdrawal(txn, str(exc))
    txn.transfer_code = data.get("transfer_code", "")
    status = data.get("status")
    if status == "otp":
        txn.failure_reason = "Paystack is waiting for an OTP. Turn off transfer OTP in Paystack Settings > Preferences, then finalize it in the Paystack dashboard."
    txn.save(update_fields=["transfer_code", "failure_reason"])
    if status == "success":
        return _succeed_withdrawal(txn)
    if status in ("failed", "reversed"):
        return _fail_withdrawal(txn, data.get("reason") or f"Paystack transfer {status}")
    return txn  # pending / otp: the webhook finishes it


def _handle_transfer_event(event, data):
    reference = (data.get("reference") or "").lower()
    txn = Transaction.objects.filter(kind="withdrawal", reference__iexact=reference).first()
    if not txn:
        logger.warning(f"Paystack {event} for unknown reference {reference}")
        return
    if event == "transfer.success":
        _succeed_withdrawal(txn)
    else:
        _fail_withdrawal(txn, data.get("reason") or event)


def handle_webhook(payload):
    event = payload.get("event")
    if event in ("transfer.success", "transfer.failed", "transfer.reversed"):
        return _handle_transfer_event(event, payload.get("data") or {})
    if event != "charge.success":
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
