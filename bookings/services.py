"""All booking state changes live here so views stay thin and rules stay in one place."""
import secrets

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from bookings.models import Booking, HiringRequest, Negotiation
from core.utils import money, notify


def _other_user(hr, party):
    return hr.worker.user if party == "employer" else hr.job.employer.user


def _require_party(obj, user, allowed=("employer", "worker")):
    party = obj.party_of(user)
    if party is None or party not in allowed:
        raise PermissionDenied("You are not part of this booking." if isinstance(obj, Booking) else
                               "You are not part of this hiring request.")
    return party


def _require_status(booking, *statuses, action="do this"):
    if booking.status not in statuses:
        raise ValidationError({"error": f"You can't {action} while the booking is '{booking.get_status_display()}'."})


# --- Hiring requests --------------------------------------------------------

def open_request(job, worker, by, amount=None, message=""):
    """Employer hires a worker for a job, or a worker responds to a job. Returns the HiringRequest."""
    if job.status != "open":
        raise ValidationError({"error": "This job is no longer open."})
    if worker.status != "active":
        raise ValidationError({"error": "This worker is not verified yet."})
    if worker.user_id == job.employer.user_id:
        raise ValidationError({"error": "You can't hire yourself."})
    amount = money(amount if amount not in (None, "") else job.offered_price)
    if amount <= 0:
        raise ValidationError({"amount": "Enter an amount greater than zero."})

    with transaction.atomic():
        hr, created = HiringRequest.objects.select_for_update().get_or_create(
            job=job, worker=worker,
            defaults={"initiated_by": by, "current_amount": amount, "last_offer_by": by, "message": message},
        )
        if not created:
            if hr.status in ("accepted",):
                raise ValidationError({"error": "This request was already accepted."})
            hr.status, hr.current_amount, hr.last_offer_by = "pending", amount, by
            if message:
                hr.message = message
        if amount != job.offered_price:
            hr.status = "negotiating"
        hr.save()
        sender = job.employer.user if by == "employer" else worker.user
        Negotiation.objects.create(hiring_request=hr, sender=sender, sender_role=by, amount=amount, message=message)

    if by == "employer":
        notify(worker.user, f"New job offer: {job.title}", f"{job.employer.user.full_name} offered ₦{amount:,.0f}.",
               kind="offer", hiring_request=hr.pk, job=job.pk)
    else:
        verb = "accepted your budget" if amount == job.offered_price else f"offered ₦{amount:,.0f}"
        notify(job.employer.user, f"{worker.user.full_name} {verb}", f"For: {job.title}",
               kind="offer", hiring_request=hr.pk, job=job.pk)
    return hr


def counter_offer(hr, user, amount, message=""):
    party = _require_party(hr, user)
    if not hr.is_open:
        raise ValidationError({"error": "This request is not available for negotiation."})
    if amount in (None, ""):
        raise ValidationError({"amount": "You must input an amount, it is required."})
    amount = money(amount)
    if amount <= 0:
        raise ValidationError({"amount": "Enter an amount greater than zero."})
    with transaction.atomic():
        Negotiation.objects.create(hiring_request=hr, sender=user, sender_role=party, amount=amount, message=message)
        hr.current_amount, hr.last_offer_by, hr.status = amount, party, "negotiating"
        hr.save(update_fields=["current_amount", "last_offer_by", "status", "updated_at"])
    notify(_other_user(hr, party), f"New counter offer: ₦{amount:,.0f}", message or hr.job.title,
           kind="offer", hiring_request=hr.pk, job=hr.job_id)
    return hr


def accept_request(hr, user):
    party = _require_party(hr, user)
    if not hr.is_open:
        raise ValidationError({"error": "This request cannot be accepted."})
    if hr.last_offer_by == party:
        raise ValidationError({"error": "You made the latest offer. Wait for the other side to accept or counter."})
    job = hr.job
    if job.status != "open":
        raise ValidationError({"error": "This job already has a worker."})

    with transaction.atomic():
        hr.status = "accepted"
        hr.save(update_fields=["status", "updated_at"])
        booking = Booking.objects.create(
            hiring_request=hr,
            agreed_price=hr.current_amount,
            scheduled_date=job.scheduled_date,
            scheduled_time=job.scheduled_time,
            service_address=job.location,
            service_latitude=job.latitude,
            service_longitude=job.longitude,
        )
        job.status = "assigned"
        job.save(update_fields=["status", "updated_at"])
        others = list(job.hiring_requests.filter(status__in=HiringRequest.OPEN_STATUSES).exclude(pk=hr.pk).select_related("worker__user"))
        job.hiring_requests.filter(pk__in=[o.pk for o in others]).update(status="expired")

    for other in others:
        notify(other.worker.user, f"{job.title} has been filled", "The employer chose another worker.", kind="offer", job=job.pk)
    notify(_other_user(hr, party), f"Offer accepted at ₦{hr.current_amount:,.0f}",
           "Next step: secure payment in escrow." if party == "worker" else "The employer will secure payment next.",
           kind="booking", booking=booking.pk)
    return booking


def reject_request(hr, user):
    party = _require_party(hr, user)
    if not hr.is_open:
        raise ValidationError({"error": "This request cannot be rejected."})
    hr.status = "rejected"
    hr.save(update_fields=["status", "updated_at"])
    notify(_other_user(hr, party), "Request declined", hr.job.title, kind="offer", hiring_request=hr.pk)
    return hr


# --- Booking lifecycle ------------------------------------------------------

def update_details(booking, user, data):
    _require_party(booking, user, ("employer",))
    _require_status(booking, "pending_payment", "scheduled", action="change booking details")
    for field in ("for_someone_else", "recipient_name", "recipient_relationship", "recipient_phone",
                  "service_address", "service_latitude", "service_longitude"):
        if field in data:
            setattr(booking, field, data[field])
    if booking.for_someone_else and not (booking.recipient_name and booking.recipient_phone):
        raise ValidationError({"recipient_name": "Add the recipient's name and phone number."})
    booking.save()
    return booking


def on_escrow_funded(booking):
    booking.status = "scheduled"
    booking.save(update_fields=["status", "updated_at"])
    amount = f"₦{booking.agreed_price:,.0f}"
    notify(booking.worker.user, "Payment secured in escrow", f"{amount} for {booking.job.title} is safely held by OyaWork.",
           kind="payment", booking=booking.pk)
    notify(booking.employer.user, "Payment secured", f"{amount} is held in escrow until you confirm the job.",
           kind="payment", booking=booking.pk)


def start_journey(booking, user):
    _require_party(booking, user, ("worker",))
    _require_status(booking, "scheduled", action="start the journey")
    booking.status = "en_route"
    booking.en_route_at = timezone.now()
    booking.oyacode = f"{secrets.randbelow(10000):04d}"
    booking.oyacode_attempts = 0
    booking.save(update_fields=["status", "en_route_at", "oyacode", "oyacode_attempts", "updated_at"])
    worker = booking.worker
    worker.availability_status = "busy"
    worker.save(update_fields=["availability_status", "updated_at"])
    name = worker.user.first_name
    notify(booking.employer.user, f"{name} is on the way to your location", booking.job.title, kind="tracking", booking=booking.pk)
    notify(booking.employer.user, "Your OyaCode has been generated",
           "Give it to your worker only when they arrive at your doorstep.", kind="tracking", booking=booking.pk)
    return booking


def mark_arrived(booking, user):
    _require_party(booking, user, ("worker",))
    _require_status(booking, "en_route", action="mark arrival")
    booking.status = "arrived"
    booking.arrived_at = timezone.now()
    booking.save(update_fields=["status", "arrived_at", "updated_at"])
    notify(booking.employer.user, f"{booking.worker.user.first_name} has arrived",
           "Share your OyaCode so the job can start.", kind="tracking", booking=booking.pk)
    return booking


def verify_code(booking, user, code):
    _require_party(booking, user, ("worker",))
    _require_status(booking, "en_route", "arrived", action="verify the OyaCode")
    if booking.oyacode_attempts >= 5:
        raise ValidationError({"error": "Too many wrong codes. Ask the employer to contact support."})
    if not secrets.compare_digest(str(code or "").strip(), booking.oyacode):
        booking.oyacode_attempts += 1
        booking.save(update_fields=["oyacode_attempts", "updated_at"])
        raise ValidationError({"code": f"That code is not correct. {5 - booking.oyacode_attempts} tries left."})
    now = timezone.now()
    booking.status = "in_progress"
    booking.arrived_at = booking.arrived_at or now
    booking.started_at = now
    booking.save(update_fields=["status", "arrived_at", "started_at", "updated_at"])
    notify(booking.employer.user, "Job started", f"{booking.worker.user.first_name} verified the OyaCode and started work.",
           kind="booking", booking=booking.pk)
    return booking


def mark_completed(booking, user):
    _require_party(booking, user, ("worker",))
    _require_status(booking, "in_progress", action="mark the job completed")
    booking.status = "completed"
    booking.completed_at = timezone.now()
    booking.save(update_fields=["status", "completed_at", "updated_at"])
    notify(booking.employer.user, f"{booking.worker.user.first_name} is finished!",
           "Inspect the work, then confirm to release payment.", kind="booking", booking=booking.pk)
    return booking


def confirm_and_release(booking, user):
    from accounts.services import refresh_employer_stats, refresh_worker_stats
    from payments.services import release_escrow

    _require_party(booking, user, ("employer",))
    _require_status(booking, "completed", action="release payment")
    with transaction.atomic():
        release_escrow(booking)
        booking.status = "confirmed"
        booking.confirmed_at = timezone.now()
        booking.save(update_fields=["status", "confirmed_at", "updated_at"])
        job = booking.job
        job.status = "completed"
        job.save(update_fields=["status", "updated_at"])
        worker = booking.worker
        worker.availability_status = "available"
        worker.save(update_fields=["availability_status", "updated_at"])
    refresh_worker_stats(worker)
    refresh_employer_stats(booking.employer)
    notify(worker.user, f"Payment released - ₦{booking.agreed_price:,.0f}", "The money is now in your OyaWork wallet.",
           kind="payment", booking=booking.pk)
    return booking


def cancel_booking(booking, user, reason=""):
    from accounts.services import refresh_worker_stats
    from payments.services import release_escrow

    party = _require_party(booking, user)
    _require_status(booking, "pending_payment", "scheduled", "en_route", "arrived", action="cancel")
    with transaction.atomic():
        if hasattr(booking, "escrow") and booking.escrow.status == "held":
            release_escrow(booking, to_worker_amount=0, to_employer_amount=booking.escrow.amount, outcome_status="refunded")
        booking.status = "cancelled"
        booking.cancelled_at = timezone.now()
        booking.cancelled_by_role = party
        booking.cancel_reason = reason[:255]
        booking.save()
        hr = booking.hiring_request
        hr.status = "cancelled"
        hr.save(update_fields=["status", "updated_at"])
        job = booking.job
        job.status = "open"  # the employer can pick someone else
        job.save(update_fields=["status", "updated_at"])
        worker = booking.worker
        worker.availability_status = "available"
        worker.save(update_fields=["availability_status", "updated_at"])
    if party == "worker":
        refresh_worker_stats(worker)
    other = booking.employer.user if party == "worker" else worker.user
    notify(other, "Booking cancelled", reason or booking.job.title, kind="booking", booking=booking.pk)
    return booking
