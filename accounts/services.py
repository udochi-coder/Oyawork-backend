from django.conf import settings
from django.db.models import Avg, Count
from django.utils import timezone

from accounts.models import Employer, VerificationDocument, Worker
from core.utils import email_enabled, sms_enabled


def verification_summary(user):
    from accounts import identity

    all_docs = list(user.verification_documents.all())
    docs = [d for d in all_docs if d.status != "rejected"]

    def state(types):
        matching = [d for d in docs if d.doc_type in types]
        if any(d.status == "approved" for d in matching):
            return "verified"
        if matching:
            return "submitted"
        return "missing"

    def note(types):
        # Newest document of this kind (docs are ordered newest first): its reviewer/Dojah note.
        # Only rejection notes are shown as written; review notes can name whoever owns a NIN, so the
        # person just hears that it's being checked by hand.
        latest = next((d for d in all_docs if d.doc_type in types), None)
        if not latest or latest.status == "approved":
            return ""
        if latest.status == "rejected":
            return latest.note
        return "We're checking this by hand. It usually takes about a day." if latest.provider_result else ""

    auto_check = identity.enabled()
    summary = {
        "phone": "verified" if user.phone_verified else "missing",
        "email": "verified" if user.email_verified else "missing",
        # Which ways of receiving a code are switched on (test mode shows codes on screen).
        "channels": {"sms": sms_enabled() or settings.DEMO_OTP, "email": email_enabled() or settings.DEMO_OTP},
        "identity": state(VerificationDocument.IDENTITY_TYPES),
        "face": state(VerificationDocument.FACE_TYPES),
        "address": state(VerificationDocument.ADDRESS_TYPES),
        # With automatic NIN matching on, the selfie is required (it's compared with the NIN photo).
        "face_required": auto_check,
        "notes": {
            "identity": note(VerificationDocument.IDENTITY_TYPES),
            "face": note(VerificationDocument.FACE_TYPES),
            "address": note(VerificationDocument.ADDRESS_TYPES),
        },
    }
    summary["complete"] = all(summary[k] != "missing" for k in required_checks())
    return summary


def required_checks():
    from accounts import identity

    return ["identity", "address"] + (["face"] if identity.enabled() else [])


def has_required_documents(user):
    return verification_summary(user)["complete"]


def activate_if_verified(user):
    """Turn pending profiles active once every required document is approved."""
    summary = verification_summary(user)
    if not all(summary[k] == "verified" for k in required_checks()):
        return False
    for profile in profiles_of(user):
        if profile.status == "pending":
            profile.status = "active"
            profile.save(update_fields=["status", "updated_at"])
    return True


def maybe_activate_profiles(user):
    """Run the automatic ID check (Dojah, if configured), auto-approve what AUTO_APPROVE_VERIFICATION
    allows, then activate the profile if everything required is approved."""
    from accounts import identity

    identity.check_identity(user)
    if not has_required_documents(user):
        return False
    if settings.AUTO_APPROVE_VERIFICATION:
        pending = user.verification_documents.filter(status="pending")
        if identity.enabled():
            # Identity is Dojah's call (or an admin's when Dojah flags it), never auto-approved.
            pending = pending.exclude(doc_type__in=VerificationDocument.IDENTITY_TYPES + VerificationDocument.FACE_TYPES)
        pending.update(status="approved", reviewed_at=timezone.now())
    return activate_if_verified(user)


def profiles_of(user):
    profiles = []
    for attr in ("worker_profile", "employer_profile"):
        try:
            profiles.append(getattr(user, attr))
        except (Worker.DoesNotExist, Employer.DoesNotExist):
            pass
    return profiles


def get_worker(user):
    try:
        return user.worker_profile
    except Worker.DoesNotExist:
        return None


def get_employer(user):
    try:
        return user.employer_profile
    except Employer.DoesNotExist:
        return None


def ensure_profile(user, role):
    """Create the profile for `role`, copying status + location from the other profile."""
    model = Worker if role == "worker" else Employer
    existing = get_worker(user) if role == "worker" else get_employer(user)
    if existing:
        return existing
    other = get_employer(user) if role == "worker" else get_worker(user)
    defaults = {}
    if other:
        defaults = {
            "status": other.status if other.status in ("active", "pending") else "pending",
            "address": other.address,
            "location": other.location,
            "latitude": other.latitude,
            "longitude": other.longitude,
        }
    return model.objects.create(user=user, **defaults)


def refresh_rating(profile, reviewee_user, as_role):
    from reviews.models import Review

    agg = Review.objects.filter(reviewee=reviewee_user, reviewee_role=as_role).aggregate(avg=Avg("rating"), n=Count("id"))
    profile.rating = round(agg["avg"] or 0, 2)
    profile.review_count = agg["n"] or 0
    profile.save(update_fields=["rating", "review_count", "updated_at"])


def refresh_worker_stats(worker):
    """Reliability = share of accepted bookings the worker saw through (cancellations by the worker and
    disputes upheld against them count against it)."""
    from bookings.models import Booking

    bookings = Booking.objects.filter(hiring_request__worker=worker)
    done = bookings.filter(status="confirmed").count()
    let_down = bookings.filter(status="cancelled", cancelled_by_role="worker").count()
    lost_disputes = bookings.filter(dispute_outcome="refund_employer").count()
    total = done + let_down + lost_disputes
    worker.completed_jobs = done
    worker.reliability_score = round(100 * done / total, 2) if total else 0
    worker.save(update_fields=["completed_jobs", "reliability_score", "updated_at"])


def refresh_employer_stats(employer):
    from bookings.models import Booking

    employer.completed_jobs = Booking.objects.filter(hiring_request__job__employer=employer, status="confirmed").count()
    employer.save(update_fields=["completed_jobs", "updated_at"])
