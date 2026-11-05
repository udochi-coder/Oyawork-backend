from django.db import IntegrityError
from loguru import logger
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from accounts.serializers import PublicWorkerSerializer
from accounts.services import get_employer, get_worker
from bookings import services as booking_services
from bookings.models import HiringRequest
from bookings.serializers import HiringRequestSerializer, OfferSerializer
from core.constants import ROLE_ADMIN, ROLE_EMPLOYER, ROLE_WORKER
from core.permissions import IsWorker, IsWorkerCanReadEmployerCanWrite, ReadOnlyOrAdmin
from core.utils import haversine_km, notify
from jobs import matching
from jobs.models import Category, Jobs, Skills
from jobs.serializers import CategorySerializer, JobSerializer, SkillsSerializer


class CategoryViewSet(ModelViewSet):
    permission_classes = [ReadOnlyOrAdmin]
    queryset = Category.objects.prefetch_related("skills")
    serializer_class = CategorySerializer


class SkillViewSet(ModelViewSet):
    permission_classes = [ReadOnlyOrAdmin]
    queryset = Skills.objects.select_related("category")
    serializer_class = SkillsSerializer


class JobViewSet(ModelViewSet):
    """Jobs.

    Employer: their own jobs (?status=open).
    Worker:   open jobs near them that they haven't declined, plus jobs they're already talking about.
              ?scope=all skips the distance filter, ?match=skills only shows jobs that need their skills.
    """

    permission_classes = [IsWorkerCanReadEmployerCanWrite]
    serializer_class = JobSerializer

    def get_permissions(self):
        if self.action in ("respond", "decline"):
            return [IsWorker()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        qs = Jobs.objects.select_related("employer__user", "category").prefetch_related("required_skills")
        role = getattr(user, "role", None)
        params = self.request.query_params

        if role == ROLE_ADMIN or user.is_staff:
            return qs

        if role == ROLE_EMPLOYER:
            employer = get_employer(user)
            if not employer:
                return qs.none()
            qs = qs.filter(employer=employer)
            if params.get("status"):
                qs = qs.filter(status__in=params["status"].split(","))
            return qs

        if role == ROLE_WORKER:
            worker = get_worker(user)
            if not worker or worker.status != "active":
                return qs.none()
            mine = HiringRequest.objects.filter(worker=worker)
            if self.action != "list":
                return qs.filter(pk__in=list(mine.values_list("job_id", flat=True))) | qs.filter(status="open")
            declined = mine.filter(status__in=["rejected", "cancelled", "expired"]).values_list("job_id", flat=True)
            qs = qs.filter(status="open").exclude(employer__user=user).exclude(pk__in=declined)
            if params.get("match") == "skills":
                skill_ids = list(worker.skills.values_list("id", flat=True))
                qs = qs.filter(required_skills__in=skill_ids) | qs.filter(category__skills__in=skill_ids)
                qs = qs.distinct()
            return qs

        return qs.none()

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        worker = get_worker(request.user) if request.user.role == ROLE_WORKER else None
        if not worker:
            return Response(self.get_serializer(qs, many=True).data)
        jobs = []
        for job in qs[:300]:
            job.distance_km = haversine_km(worker.latitude, worker.longitude, job.latitude, job.longitude)
            if request.query_params.get("scope") != "all" and not matching.worker_can_see(job, worker):
                continue
            jobs.append(job)
        jobs.sort(key=lambda j: (j.distance_km is None, j.distance_km or 0))
        return Response(self.get_serializer(jobs, many=True).data)

    def retrieve(self, request, *args, **kwargs):
        job = self.get_object()
        me = get_worker(request.user) or get_employer(request.user)
        if me:
            job.distance_km = haversine_km(me.latitude, me.longitude, job.latitude, job.longitude)
        return Response(self.get_serializer(job).data)

    def create(self, request, *args, **kwargs):
        employer = get_employer(request.user)
        if not employer:
            logger.warning(f"Non employer user_id={request.user.id} attempted to post a job without an employer profile")
            return Response({"error": "You must have an employer profile before posting a job"}, status=status.HTTP_403_FORBIDDEN)
        if employer.status != "active":
            return Response({"error": "Finish verifying your account before posting a job."}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"Job post failed - employer={employer.id}, errors={serializer.errors}")
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            extra = {"employer": employer, "is_posted": True}
            if serializer.validated_data.get("latitude") is None and employer.latitude is not None:
                extra.update(latitude=employer.latitude, longitude=employer.longitude)
            if not serializer.validated_data.get("location") and employer.location:
                extra["location"] = employer.location
            job = serializer.save(**extra)
        except IntegrityError:
            logger.error(f"Duplicate post (race condition) - employer={employer.id}")
            return Response({"errors": "You have already posted this job"}, status=status.HTTP_409_CONFLICT)

        matches = matching.matching_workers(job)
        for worker in matches:
            distance = f"{worker.distance_km} km away" if worker.distance_km is not None else "Near you"
            notify(worker.user, f"New job nearby: {job.title}", f"₦{job.offered_price:,.0f} - {job.location or ''} - {distance}",
                   kind="job", job=job.pk)
        logger.success(
            f"Job post created - id={job.id}, title={job.title}, category={job.category.name}, "
            f"offered_price={job.offered_price}, location={job.location}, matched_workers={len(matches)}"
        )
        data = self.get_serializer(job).data
        data["matched_workers"] = len(matches)
        return Response(data, status=status.HTTP_201_CREATED)

    def _own_job(self):
        job = self.get_object()
        employer = get_employer(self.request.user)
        if not employer or job.employer_id != employer.id:
            raise PermissionDenied("You can only change a job you posted yourself.")
        return job

    def update(self, request, *args, **kwargs):
        job = self._own_job()
        if job.status != "open":
            raise ValidationError({"error": "Only open jobs can be edited."})
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        job = self._own_job()
        if job.hiring_requests.filter(status="accepted").exists():
            raise PermissionDenied("This job already has a booking. Cancel the booking instead.")
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self._own_job()
        if job.status != "open":
            raise ValidationError({"error": "Only open jobs can be cancelled here."})
        job.status = "cancelled"
        job.save(update_fields=["status", "updated_at"])
        for hr in job.hiring_requests.filter(status__in=HiringRequest.OPEN_STATUSES).select_related("worker__user"):
            hr.status = "cancelled"
            hr.save(update_fields=["status", "updated_at"])
            notify(hr.worker.user, f"{job.title} was cancelled", kind="job", job=job.pk)
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["get"])
    def matches(self, request, pk=None):
        """'Finding the right workers near you...' screen."""
        job = self._own_job()
        workers = matching.matching_workers(job, limit=50)
        return Response({
            "count": len(workers),
            "criteria": matching.criteria(job),
            "workers": PublicWorkerSerializer(workers, many=True, context={"request": request}).data,
        })

    @action(detail=True, methods=["get"])
    def interested(self, request, pk=None):
        """'Workers Interested' screen: open offers from workers for this job."""
        job = self._own_job()
        qs = job.hiring_requests.select_related("worker__user", "job__employer__user", "job__category").prefetch_related(
            "negotiations__sender", "worker__skills"
        ).exclude(status__in=["rejected", "cancelled", "expired"])
        return Response(HiringRequestSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        """Worker accepts the employer's budget (no amount) or makes a counter offer (amount)."""
        worker = get_worker(request.user)
        if not worker or request.user.role != ROLE_WORKER:
            raise PermissionDenied("Switch to Worker mode to respond to jobs.")
        if worker.status != "active":
            raise PermissionDenied("Finish verifying your account before taking jobs.")
        job = self.get_object()
        data = OfferSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        hr = booking_services.open_request(job, worker, "worker", data.validated_data.get("amount"), data.validated_data["message"])
        return Response(HiringRequestSerializer(hr, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        worker = get_worker(request.user)
        if not worker:
            raise PermissionDenied("Switch to Worker mode first.")
        job = self.get_object()
        hr, _ = HiringRequest.objects.get_or_create(
            job=job, worker=worker,
            defaults={"initiated_by": "worker", "current_amount": job.offered_price, "last_offer_by": "worker", "status": "rejected"},
        )
        if hr.status in HiringRequest.OPEN_STATUSES:
            booking_services.reject_request(hr, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
