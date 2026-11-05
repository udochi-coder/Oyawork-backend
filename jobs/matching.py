"""Smart job matching: who should see a job request."""
from django.conf import settings
from django.db.models import Q

from core.utils import haversine_km

MIN_RELIABILITY = float(getattr(settings, "MATCH_MIN_RELIABILITY", 70))
# Used for jobs or workers without a saved location.
DEFAULT_RADIUS_KM = 10


def criteria(job):
    return [
        {"key": "verified", "label": "Verified profile"},
        {"key": "available", "label": "Currently available"},
        {"key": "nearby", "label": f"Nearby (within {job.preferred_radius} km)"},
        {"key": "skill", "label": "Required skill"},
        {"key": "reliability", "label": f"Reliability score above {int(MIN_RELIABILITY)}%"},
    ]


def matching_workers(job, limit=200):
    from accounts.models import Worker

    skills = list(job.required_skills.all())
    qs = Worker.objects.filter(status="active", availability_status="available", user__is_active=True)
    qs = qs.exclude(user=job.employer.user)
    if skills:
        qs = qs.filter(skills__in=skills)
    else:
        qs = qs.filter(skills__category=job.category)
    # New workers (no completed jobs yet) get a chance; experienced ones must keep their score up.
    qs = qs.filter(Q(completed_jobs=0) | Q(reliability_score__gte=MIN_RELIABILITY))
    qs = qs.select_related("user").distinct()

    matches = []
    for worker in qs[:1000]:
        distance = haversine_km(job.latitude, job.longitude, worker.latitude, worker.longitude)
        if distance is not None and distance > min(job.preferred_radius, worker.preferred_radius or DEFAULT_RADIUS_KM):
            continue
        worker.distance_km = distance
        matches.append(worker)
        if len(matches) >= limit:
            break
    matches.sort(key=lambda w: (w.distance_km is None, w.distance_km or 0))
    return matches


def worker_can_see(job, worker):
    if job.status != "open":
        return False
    distance = haversine_km(job.latitude, job.longitude, worker.latitude, worker.longitude)
    return distance is None or distance <= max(job.preferred_radius, worker.preferred_radius)
