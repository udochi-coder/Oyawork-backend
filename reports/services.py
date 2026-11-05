from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.utils import notify
from payments.models import Escrow
from reports.models import Dispute


def open_dispute(booking, user, reason, details, files=()):
    party = booking.party_of(user)
    if party is None:
        raise PermissionDenied("You are not part of this booking.")
    if booking.status in ("pending_payment", "cancelled"):
        raise ValidationError({"error": "There is nothing to dispute on this booking yet."})
    if booking.disputes.exclude(status="resolved").exists():
        raise ValidationError({"error": "There is already an open dispute for this booking."})
    with transaction.atomic():
        dispute = Dispute.objects.create(
            booking=booking, raised_by=user, raised_by_role=party, reason=reason, details=details,
            previous_booking_status=booking.status,
        )
        for f in files:
            dispute.evidence.create(file=f, uploaded_by=user)
        escrow = Escrow.objects.filter(booking=booking, status="held").first()
        if escrow:
            escrow.status = "frozen"
            escrow.save(update_fields=["status"])
            dispute.status = "payment_held"
            dispute.save(update_fields=["status"])
        booking.status = "disputed"
        booking.save(update_fields=["status", "updated_at"])
    other = booking.worker.user if party == "employer" else booking.employer.user
    notify(other, f"A dispute was opened (#{dispute.ticket})",
           "OyaWork support will contact you. Payment is held until it's resolved.", kind="dispute", dispute=dispute.pk)
    notify(user, f"Dispute #{dispute.ticket} submitted", "Expected resolution window: 24 hours.", kind="dispute", dispute=dispute.pk)
    return dispute


def resolve_dispute(dispute, resolution, note="", worker_share=None):
    """Admin decision. Moves escrow money and closes the booking."""
    from accounts.services import refresh_worker_stats
    from payments.services import release_escrow

    booking = dispute.booking
    with transaction.atomic():
        escrow = Escrow.objects.filter(booking=booking, status__in=["held", "frozen"]).first()
        if escrow and resolution == "release_worker":
            release_escrow(booking)
            booking.status = "confirmed"
            booking.confirmed_at = timezone.now()
        elif escrow and resolution == "refund_employer":
            release_escrow(booking, to_worker_amount=0, to_employer_amount=escrow.amount, outcome_status="refunded")
            booking.status = "cancelled"
            booking.cancelled_at = timezone.now()
        elif escrow and resolution == "split":
            share = escrow.amount / 2 if worker_share is None else worker_share
            release_escrow(booking, to_worker_amount=share, to_employer_amount=escrow.amount - share, outcome_status="split")
            booking.status = "confirmed"
        else:
            if escrow:
                escrow.status = "held"
                escrow.save(update_fields=["status"])
            booking.status = dispute.previous_booking_status or "completed"
        booking.dispute_outcome = resolution if resolution != "no_action" else ""
        booking.save()
        dispute.status = "resolved"
        dispute.resolution = resolution
        dispute.resolution_note = note
        dispute.resolved_at = timezone.now()
        dispute.save()
        worker = booking.worker
        if booking.status in ("confirmed", "cancelled"):
            worker.availability_status = "available"
            worker.save(update_fields=["availability_status", "updated_at"])
    refresh_worker_stats(booking.worker)
    for user in (booking.worker.user, booking.employer.user):
        notify(user, f"Dispute #{dispute.ticket} resolved", dispute.get_resolution_display(), kind="dispute", dispute=dispute.pk)
    return dispute
